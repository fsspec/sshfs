import hashlib
import os
import posixpath
import secrets
import tempfile
import warnings
from concurrent import futures
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import fsspec
import pytest
from asyncssh.misc import ChannelOpenError
from asyncssh.sftp import (
    SFTPAttrs,
    SFTPClient,
    SFTPFailure,
    SFTPOpUnsupported,
    SFTPPermissionDenied,
)
from fsspec.asyn import sync
from importlib_metadata import entry_points

from sshfs import SSHFileSystem
from sshfs.file import SSHFile
from sshfs.utils import READ_BLOCK_SIZE, WRITE_BLOCK_SIZE

_STATIC = (Path(__file__).parent / "static").resolve()
USERS = {"user": _STATIC / "user.key"}

DEVIATION = 5


@pytest.fixture(scope="session")
def ssh_server():
    with warnings.catch_warnings():
        # Somewhere in the 'invoke' library, there is an import of
        # deprecated 'imp' module so we simply ignore that warning.
        warnings.simplefilter("ignore", category=DeprecationWarning)
        import mockssh

    with mockssh.Server(USERS) as server:
        yield server


@pytest.fixture(scope="session")
def base_remote_dir():
    with tempfile.TemporaryDirectory() as path:
        yield path


@pytest.fixture
def remote_dir(fs, base_remote_dir, request):
    directory = posixpath.join(base_remote_dir, request.node.name).rstrip("/")
    fs.mkdir(directory)
    yield directory


@pytest.fixture
def fs(ssh_server, user="user"):
    yield SSHFileSystem(
        host=ssh_server.host,
        port=ssh_server.port,
        username=user,
        client_keys=[USERS[user]],
    )


@pytest.fixture
def fs_hard_queue(ssh_server, user="user"):
    from sshfs import SFTPHardChannelPool

    yield SSHFileSystem(
        host=ssh_server.host,
        port=ssh_server.port,
        username=user,
        client_keys=[USERS[user]],
        pool_type=SFTPHardChannelPool,
    )


def strip_keys(info):
    for key in ["name", "time", "mtime", "atime"]:
        info.pop(key, None)
    return info


def test_fsspec_registration(ssh_server):
    for ep in list(entry_points(group="fsspec.specs")):
        fs = fsspec.filesystem(
            ep.name,
            host=ssh_server.host,
            port=ssh_server.port,
            username="user",
            client_keys=[USERS["user"]],
        )
        assert isinstance(fs, SSHFileSystem)


def test_fsspec_url_parsing(ssh_server, remote_dir, user="user"):
    for ep in list(entry_points(group="fsspec.specs")):
        url = f"{ep.name}://{user}@{ssh_server.host}:{ssh_server.port}/{remote_dir}/file"
        with fsspec.open(url, "w", client_keys=[USERS[user]]) as file:
            # Check the underlying file system.
            file_fs = file.buffer.fs
            assert isinstance(file_fs, SSHFileSystem)
            assert file_fs.storage_options == {
                "host": ssh_server.host,
                "port": ssh_server.port,
                "username": user,
                "client_keys": [USERS[user]],
            }


def test_sftp_client_kwargs(ssh_server, base_remote_dir, user="user"):
    fs = SSHFileSystem(
        host=ssh_server.host,
        port=ssh_server.port,
        username=user,
        client_keys=[USERS[user]],
        sftp_client_kwargs={"sftp_version": 3},
    )
    assert fs._pool.sftp_client_kwargs == {"sftp_version": 3}

    file = posixpath.join(base_remote_dir, "sftp_client_kwargs_probe")
    fs.touch(file)
    assert fs.exists(file)


def test_sftp_client_kwargs_path_encoding(
    ssh_server, base_remote_dir, user="user"
):
    # path_encoding=None makes asyncssh deliver remote paths as raw
    # bytes instead of str, for servers with non-UTF-8 file names (#39).
    fs = SSHFileSystem(
        host=ssh_server.host,
        port=ssh_server.port,
        username=user,
        client_keys=[USERS[user]],
        sftp_client_kwargs={"path_encoding": None},
    )

    directory = Path(base_remote_dir) / "path_encoding_probe"
    directory.mkdir()
    (directory / "data.txt").write_bytes(b"data")

    encoded = str(directory).encode()
    assert fs.ls(encoded, detail=False) == [encoded + b"/data.txt"]
    assert fs.cat_file(encoded + b"/data.txt") == b"data"


def test_info(fs, remote_dir):
    fs.touch(remote_dir + "/a.txt")
    details = fs.info(remote_dir + "/a.txt")
    assert details["type"] == "file"
    assert details["name"] == remote_dir + "/a.txt"
    assert details["size"] == 0

    fs.mkdir(remote_dir + "/dir")
    details = fs.info(remote_dir + "/dir")
    assert details["type"] == "directory"
    assert details["name"] == remote_dir + "/dir/"

    details = fs.info(remote_dir + "/dir/")
    assert details["name"] == remote_dir + "/dir/"


def test_info_timestamps_are_tz_aware_utc(fs, remote_dir):
    fs.touch(remote_dir + "/a.txt")
    details = fs.info(remote_dir + "/a.txt")
    for key in ["time", "mtime"]:
        assert details[key].tzinfo == timezone.utc


def test_decode_attributes_missing_timestamps(fs):
    attrs = SFTPAttrs(
        permissions=0o100644, size=0, uid=0, gid=0, atime=None, mtime=None
    )
    details = fs._decode_attributes(attrs)
    assert details["time"] is None
    assert details["mtime"] is None


def test_move(fs, remote_dir):
    fs.touch(remote_dir + "/a.txt")
    initial_info = fs.info(remote_dir + "/a.txt")

    fs.move(remote_dir + "/a.txt", remote_dir + "/b.txt")
    secondary_info = fs.info(remote_dir + "/b.txt")

    assert not fs.exists(remote_dir + "/a.txt")
    assert fs.exists(remote_dir + "/b.txt")

    initial_info.pop("name")
    secondary_info.pop("name")

    initial_mtime = initial_info.pop("mtime")
    initial_atime = initial_info.pop("time")
    secondary_mtime = secondary_info.pop("mtime")
    secondary_atime = secondary_info.pop("time")
    assert abs(secondary_mtime - initial_mtime) <= timedelta(
        seconds=1
    ), "mtime differs more than expected"
    assert abs(secondary_atime - initial_atime) <= timedelta(
        seconds=1
    ), "atime differs more than expected"
    assert initial_info == secondary_info


def test_copy(fs, remote_dir):
    data = b"data to copy"
    with fs.open(remote_dir + "/a.txt", "wb") as stream:
        stream.write(data)
    initial_info = fs.info(remote_dir + "/a.txt")

    fs.copy(remote_dir + "/a.txt", remote_dir + "/b.txt")
    secondary_info = fs.info(remote_dir + "/b.txt")

    assert fs.exists(remote_dir + "/a.txt")
    assert fs.exists(remote_dir + "/b.txt")
    assert fs.cat_file(remote_dir + "/b.txt") == data

    assert strip_keys(initial_info) == strip_keys(secondary_info)


class _FakeChannelPool:
    def __init__(self, channel):
        self.channel = channel

    def get(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.channel

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.fixture(scope="session", params=[None, 4], ids=["sftpv3", "sftpv4"])
def copydata_fs(asyncssh_server, request):
    # v4+ separates the file type from `permissions`, so both protocol
    # generations must satisfy the same contract.
    host, port, _root = asyncssh_server
    extra = {}
    if request.param is not None:
        extra["sftp_client_kwargs"] = {"sftp_version": request.param}
    fs = SSHFileSystem(
        host=host,
        port=port,
        username="user",
        client_keys=[USERS["user"]],
        **extra,
    )
    yield fs
    # Close the connection so the server fixture can shut its loop
    # down without cancelling live connection tasks.
    with suppress(Exception):
        sync(fs.loop, fs._stack.aclose, timeout=5)


@pytest.fixture
def copydata_dir(asyncssh_server, request):
    _host, _port, root = asyncssh_server
    # unique per invocation so pytest-rerunfailures retries get a
    # fresh directory
    local = root / f"{request.node.name}-{secrets.token_hex(4)}"
    local.mkdir()
    # the server is chrooted to `root`, so `local` is served as this
    # remote path
    yield local, "/" + local.name


requires_copy_data = pytest.mark.skipif(
    not hasattr(SFTPClient, "supports_remote_copy"),
    reason="asyncssh without copy-data support (< 2.19)",
)


@requires_copy_data
def test_cp_file_copy_data_creates(copydata_fs, copydata_dir):
    fs = copydata_fs
    local, remote = copydata_dir
    (local / "src").write_bytes(b"payload")
    (local / "src").chmod(0o666)

    umask = os.umask(0)
    os.umask(umask)

    fs.cp_file(remote + "/src", remote + "/dst")
    # the copy-data path was actually taken, not the shell fallback
    assert fs._supports_remote_copy is True
    assert (local / "dst").read_bytes() == b"payload"
    # a new file gets the source's mode filtered by the server's
    # umask, like cp
    assert ((local / "dst").stat().st_mode & 0o7777) == 0o666 & ~umask

    # "copy into" an existing directory creates the source's basename
    (local / "d").mkdir()
    fs.cp_file(remote + "/src", remote + "/d")
    assert (local / "d" / "src").read_bytes() == b"payload"


@requires_copy_data
def test_cp_file_copy_data_ignores_reported_size(zero_size_server):
    # Sources whose stat lies about the size (procfs, sysfs) must be
    # copied whole: the copy runs to the source's real end of file and
    # never sizes the destination from a stat snapshot.
    host, port, root = zero_size_server
    fs = SSHFileSystem(
        host=host, port=port, username="user", client_keys=[USERS["user"]]
    )
    try:
        (root / "src").write_bytes(b"payload" * 1000)
        assert fs.info("/src")["size"] == 0

        fs.cp_file("/src", "/dst")
        assert fs._supports_remote_copy is True
        assert (root / "dst").read_bytes() == b"payload" * 1000
    finally:
        with suppress(Exception):
            sync(fs.loop, fs._stack.aclose, timeout=5)


def test_remote_copy_keeps_mode_zero(fs, monkeypatch):
    # A mode of 0 is a valid mode, not a missing one: it must be
    # requested as-is instead of falling back to the server's default
    # (SFTP v4+ reports it as permissions == 0, since the file type
    # lives in a separate field).
    opened = []

    class _File:
        async def stat(self):
            return SFTPAttrs(permissions=0)

        async def close(self):
            pass

    class Channel:
        supports_remote_copy = True

        def encode(self, path):
            return path.encode() if isinstance(path, str) else path

        async def isdir(self, path):
            return False

        async def open(self, path, *args, **kwargs):
            opened.append((path, args))
            return _File()

        async def remote_copy(self, src, dst):
            pass

    monkeypatch.setattr(fs, "_supports_remote_copy", True)
    monkeypatch.setattr(fs, "_pool", _FakeChannelPool(Channel()))

    fs.cp_file("/src", "/dst")
    _dst_path, dst_args = opened[-1]
    assert dst_args[1].permissions == 0


@requires_copy_data
def test_cp_file_copy_data_existing_destinations(copydata_fs, copydata_dir):
    # The extension path only creates destinations. Anything existing
    # -- including every alias of the source -- is left to the shell
    # fallback, which this server does not offer: the copy must fail
    # without touching a single byte.
    fs = copydata_fs
    local, remote = copydata_dir
    src = local / "src"
    src.write_bytes(b"payload")

    (local / "existing").write_bytes(b"old")
    (local / "link").symlink_to("src")
    os.link(src, local / "hard")

    for dst in ["/src", "/existing", "/link", "/hard"]:
        with pytest.raises((OSError, ChannelOpenError)):
            fs.cp_file(remote + "/src", remote + dst)
    with pytest.raises((OSError, ChannelOpenError)):
        fs.cp_file((remote + "/src").encode(), remote + "/src")

    assert src.read_bytes() == b"payload"
    assert (local / "existing").read_bytes() == b"old"
    assert (local / "hard").stat().st_ino == src.stat().st_ino
    # "copy into" resolving to an existing file is refused the same way
    (local / "d").mkdir()
    os.link(src, local / "d" / "src")
    with pytest.raises((OSError, ChannelOpenError)):
        fs.cp_file(remote + "/src", remote + "/d")
    assert (local / "d" / "src").read_bytes() == b"payload"


@requires_copy_data
def test_cp_file_copy_data_never_redirects(copydata_fs, copydata_dir):
    # FXF_EXCL refuses to create through a dangling symlink, so the
    # copy cannot be redirected to the link's target. (Trailing-slash
    # handling is the server's path resolution and is not asserted:
    # this server normalizes it away, OpenSSH rejects it.)
    fs = copydata_fs
    local, remote = copydata_dir
    (local / "src").write_bytes(b"payload")
    (local / "dangling").symlink_to("missing")

    with pytest.raises((OSError, ChannelOpenError)):
        fs.cp_file(remote + "/src", remote + "/dangling")
    assert not (local / "missing").exists()


def test_mv_hardlink_alias(copydata_fs, copydata_dir):
    # POSIX rename between two names of the same inode is a no-op:
    # the move succeeds with both names surviving and no data lost.
    fs = copydata_fs
    local, remote = copydata_dir
    src = local / "src"
    src.write_bytes(b"payload")
    os.link(src, local / "hard")

    fs.mv(remote + "/src", remote + "/hard")
    assert src.read_bytes() == b"payload"
    assert (local / "hard").read_bytes() == b"payload"


def test_cp_file_copy_data_denied(fs, monkeypatch):
    # copy-data advertised but denied by server policy: the capability
    # is re-cached as unsupported and the copy falls back to the shell,
    # which overwrites the empty file created by the exclusive open.
    events = []

    class _File:
        async def stat(self):
            return SFTPAttrs(permissions=0o100644)

        async def close(self):
            events.append("close")

    class _OpenResult:
        def __init__(self):
            self.file = _File()

        def __await__(self):
            async def _result():
                return self.file

            return _result().__await__()

        async def __aenter__(self):
            return self.file

        async def __aexit__(self, *exc):
            return False

    class Channel:
        supports_remote_copy = True

        def encode(self, path):
            return path.encode() if isinstance(path, str) else path

        async def isdir(self, path):
            return False

        def open(self, path, *args, **kwargs):
            events.append(("open", path))
            return _OpenResult()

        async def remote_copy(self, src, dst):
            raise SFTPPermissionDenied("denied by policy")

    async def record_shell(cmd, **kwargs):
        events.append(("shell", cmd))

    monkeypatch.setattr(fs, "_supports_remote_copy", None)
    monkeypatch.setattr(fs, "_pool", _FakeChannelPool(Channel()))
    monkeypatch.setattr(fs, "_execute", record_shell)

    fs.cp_file("/src", "/dst")
    assert ("shell", "cp /src /dst") in events
    assert fs._supports_remote_copy is False


def test_mv_fallback_keeps_source_on_copy_failure(fs, monkeypatch):
    # When posix_rename is unsupported and the copy fails, the source
    # must survive: it may only be removed after a successful copy.
    class Channel:
        async def posix_rename(self, lpath, rpath):
            raise SFTPOpUnsupported("posix-rename not supported")

    removed = []

    async def failing_cp(*args, **kwargs):
        raise OSError("copy failed")

    async def record_rm(path, **kwargs):
        removed.append(path)

    monkeypatch.setattr(fs, "_pool", _FakeChannelPool(Channel()))
    monkeypatch.setattr(fs, "_cp_file", failing_cp)
    monkeypatch.setattr(fs, "_rm_file", record_rm)

    with pytest.raises(OSError):
        fs.mv("/src", "/dst")
    assert removed == []

    async def ok_cp(*args, **kwargs):
        pass

    monkeypatch.setattr(fs, "_cp_file", ok_cp)
    fs.mv("/src", "/dst")
    assert removed == ["/src"]


@pytest.mark.parametrize("legacy_asyncssh", [False, True])
def test_cp_file_shell_fallback(fs, monkeypatch, legacy_asyncssh):
    # Channels without copy-data support -- and asyncssh < 2.19
    # channels, which lack the supports_remote_copy attribute entirely
    # -- must use the shell cp path, and only the first call may touch
    # the channel pool (the probed capability is cached).
    calls = []
    pool_uses = []

    class Channel:
        async def copy(self, *args, **kwargs):
            raise AssertionError("copy-data must not be attempted")

    if not legacy_asyncssh:
        Channel.supports_remote_copy = False

    async def record_shell(cmd, **kwargs):
        calls.append(cmd)

    pool = _FakeChannelPool(Channel())
    _orig_get = pool.get
    pool.get = lambda: pool_uses.append(1) or _orig_get()

    monkeypatch.setattr(fs, "_supports_remote_copy", None)
    monkeypatch.setattr(fs, "_pool", pool)
    monkeypatch.setattr(fs, "_execute", record_shell)

    fs.cp_file("/src", "/dst")
    fs.cp_file("/src2", "/dst2")
    assert calls == ["cp /src /dst", "cp /src2 /dst2"]
    assert len(pool_uses) == 1


def test_rm(fs, remote_dir):
    fs.touch(remote_dir + "/a.txt")
    fs.rm(remote_dir + "/a.txt")
    assert not fs.exists(remote_dir + "/a.txt")

    fs.mkdir(remote_dir + "/dir")
    fs.rm(remote_dir + "/dir")
    assert not fs.exists(remote_dir + "/dir")

    fs.mkdir(remote_dir + "/dir")
    fs.touch(remote_dir + "/dir/a")
    fs.touch(remote_dir + "/dir/b")
    fs.mkdir(remote_dir + "/dir/c/")
    fs.touch(remote_dir + "/dir/c/a/")
    fs.rm(remote_dir + "/dir", recursive=True)
    assert not fs.exists(remote_dir + "/dir")


def test_checksum(fs, remote_dir):
    data = b"iterative.ai"

    with fs.open(remote_dir + "/a.txt", "wb") as stream:
        stream.write(data)

    checksum = hashlib.md5(data).hexdigest()
    assert fs.checksum(remote_dir + "/a.txt") == checksum


def test_ls(fs, remote_dir):
    fs.mkdir(remote_dir + "dir/")
    files = set()
    for no in range(8):
        file = remote_dir + f"dir/test_{no}"
        fs.touch(file)
        files.add(file)

    assert set(fs.ls(remote_dir + "dir/", detail=False)) == files

    dirs = fs.ls(remote_dir + "dir/")
    expected = [fs.info(file) for file in files]

    by_name = lambda details: details["name"]
    dirs.sort(key=by_name)
    expected.sort(key=by_name)

    assert dirs == expected


def test_walk(fs, remote_dir):
    fs.mkdir(remote_dir + "/a")
    fs.mkdir(remote_dir + "/a/b")
    fs.touch(remote_dir + "/a/f1")
    fs.touch(remote_dir + "/a/b/f2")

    result = {
        root: (sorted(dirs), sorted(files))
        for root, dirs, files in fs.walk(remote_dir + "/a")
    }
    assert result == {
        remote_dir + "/a": (["b"], ["f1"]),
        remote_dir + "/a/b": ([], ["f2"]),
    }


def test_root(fs):
    # An explicit "/" must resolve to the filesystem root, not the
    # user's home directory.
    assert fs.exists("/")
    assert fs.isdir("/")
    assert fs.info("/")["name"] == "/"


def test_strip_protocol():
    strip = SSHFileSystem._strip_protocol
    # An explicit absolute root is preserved so walk("/") lists from the root.
    assert strip("/") == "/"
    assert strip("ssh://host/") == "/"
    # Empty and relative paths still resolve against the home directory
    # instead of being redirected to the root.
    assert strip("") == ""
    assert strip("ssh://host") == ""
    assert strip("foo/bar") == "foo/bar"
    # Regular absolute paths are unaffected.
    assert strip("/foo/bar") == "/foo/bar"
    assert strip("ssh://host/foo/bar") == "/foo/bar"


def test_mkdir(fs, remote_dir):
    fs.mkdir(remote_dir + "dir/")
    assert fs.isdir(remote_dir + "dir/")
    assert len(fs.ls(remote_dir + "dir/")) == 0

    # SFTP server yields a generic error, so we can't
    # cast it anything (it might be a permission error
    # or in this case an identifier that the directory
    # exists).
    with pytest.raises(SFTPFailure):
        fs.mkdir(remote_dir + "dir/", create_parents=False)

    fs.mkdir(remote_dir + "dir/sub", create_parents=False)
    assert fs.isdir(remote_dir + "dir/sub")


def test_makedirs(fs, remote_dir):
    fs.makedirs(remote_dir + "dir/a/b/c/")
    assert fs.isdir(remote_dir + "dir/a/b/c/")
    assert fs.isdir(remote_dir + "dir/a/b/")
    assert fs.isdir(remote_dir + "dir/a/")

    with pytest.raises(FileExistsError):
        fs.makedirs(remote_dir + "dir/a/b/c/")

    fs.makedirs(remote_dir + "dir/a/b/c/", exist_ok=True)


def test_exceptions(fs, remote_dir):
    with pytest.raises(FileNotFoundError):
        with fs.open(remote_dir + "/a.txt"):
            ...

    with pytest.raises(FileNotFoundError):
        fs.copy(remote_dir + "/u.txt", remote_dir + "/y.txt")

    fs.makedirs(remote_dir + "/dir/a/b/c")
    with pytest.raises(FileExistsError):
        fs.makedirs(remote_dir + "/dir/a/b/c")


def test_open_block_size(fs, remote_dir):
    # mockssh (paramiko) does not implement limits@openssh.com, so the
    # tuned defaults must survive asyncssh's synthesized 16 KiB floors.
    fs.touch(remote_dir + "/a.txt")
    with fs.open(remote_dir + "/a.txt", "rb") as file:
        assert file.blocksize == READ_BLOCK_SIZE * file.max_requests
    with fs.open(remote_dir + "/b.txt", "wb") as file:
        assert file.blocksize == WRITE_BLOCK_SIZE * file.max_requests
    # An explicit block_size always wins.
    with fs.open(remote_dir + "/c.txt", "wb", block_size=4096) as file:
        assert file.blocksize == 4096 * file.max_requests


def test_determine_block_size():
    reader = SimpleNamespace(readable=lambda: True)
    writer = SimpleNamespace(readable=lambda: False)
    determine = SSHFile._determine_block_size

    # The server reported its limits: use them.
    reported = SimpleNamespace(
        limits=SimpleNamespace(
            max_packet_len=262144,
            max_read_len=261120,
            max_write_len=131072,
        )
    )
    assert determine(reader, reported) == 261120
    assert determine(writer, reported) == 131072

    # No limits@openssh.com support: asyncssh synthesizes 16 KiB
    # read/write floors with max_packet_len == 0; keep the defaults.
    synthesized = SimpleNamespace(
        limits=SimpleNamespace(
            max_packet_len=0, max_read_len=16384, max_write_len=16384
        )
    )
    assert determine(reader, synthesized) == READ_BLOCK_SIZE
    assert determine(writer, synthesized) == WRITE_BLOCK_SIZE

    # asyncssh without the limits API at all.
    assert determine(reader, SimpleNamespace()) == READ_BLOCK_SIZE
    assert determine(writer, SimpleNamespace()) == WRITE_BLOCK_SIZE


def test_open_rw(fs, remote_dir):
    data = b"dvc.org"

    with fs.open(remote_dir + "/a.txt", "wb") as stream:
        stream.write(data)

    with fs.open(remote_dir + "/a.txt") as stream:
        assert stream.read() == data


def test_open_rw_flush(fs, remote_dir):
    data = b"dvc.org"

    with fs.open(remote_dir + "/b.txt", "wb") as stream:
        for _ in range(200):
            stream.write(data)
            stream.write(data)
            stream.flush()

    with fs.open(remote_dir + "/b.txt", "rb") as stream:
        assert stream.read() == data * 400


def test_open_rwa(fs, remote_dir):
    data = b"dvc.org"

    with fs.open(remote_dir + "/c.txt", "wb") as stream:
        for _ in range(200):
            stream.write(data)

    with fs.open(remote_dir + "/c.txt", "ab") as stream:
        for _ in range(200):
            stream.write(data)

    with fs.open(remote_dir + "/c.txt", "rb") as stream:
        assert stream.read() == data * 400


def test_open_r_seek(fs, remote_dir):
    data = b"dvc.org"

    with fs.open(remote_dir + "/c.txt", "wb") as stream:
        for _ in range(200):
            stream.write(data)

    with fs.open(remote_dir + "/c.txt", "rb") as stream:
        stream.seek(len(data * 100))
        assert stream.read() == data * 100


@pytest.mark.parametrize("fs", [fs, fs_hard_queue], indirect=True)
def test_concurrent_operations(fs, remote_dir):
    def create_random_file():
        name = secrets.token_hex(16)
        with fs.open(remote_dir + "/" + name, "w") as stream:
            stream.write(name)
        return name

    def read_random_file(name):
        with fs.open(remote_dir + "/" + name, "r") as stream:
            return stream.read()

    with futures.ThreadPoolExecutor() as executor:
        write_futures, _ = futures.wait(
            [executor.submit(create_random_file) for _ in range(64)],
            return_when=futures.ALL_COMPLETED,
        )
        write_names = {future.result() for future in write_futures}

        read_futures, _ = futures.wait(
            [executor.submit(read_random_file, name) for name in write_names],
            return_when=futures.ALL_COMPLETED,
        )
        read_names = {future.result() for future in read_futures}

        assert write_names == read_names


@pytest.mark.parametrize("file_path", ["a.txt", "dir/a.txt"])
def test_put_file(fs, remote_dir, file_path):
    with tempfile.NamedTemporaryFile() as file:
        file.file.write(b"data")
        file.file.flush()
        fs.put_file(file.name, remote_dir + f"/{file_path}")

    with fs.open(remote_dir + f"/{file_path}") as stream:
        assert stream.read() == b"data"


def test_concurrency_for_raw_commands(fs, remote_dir):
    with fs.open(remote_dir + "/cp_data", "wb") as stream:
        stream.write(b"hello!")

    with futures.ThreadPoolExecutor() as executor:
        cp_futures = [
            executor.submit(
                fs.cp_file,
                remote_dir + "/cp_data",
                remote_dir + f"/cp_data_{index}_{secrets.token_hex(16)}",
            )
            for index in range(16)
        ]
        for future in futures.as_completed(cp_futures):
            future.result()


def test_modified(fs, remote_dir):
    modified = fs.modified(remote_dir)
    assert isinstance(modified, datetime)


def test_cat_file_sync(fs, remote_dir):
    # Define the content to write to the test file
    test_content = b"Test content for cat_file"
    test_file_path = remote_dir + "/test_file.txt"

    # Write content to the file synchronously
    with open(test_file_path, "wb") as f:
        f.write(test_content)

    # Use the cat_file method to read the content back synchronously
    read_content = fs.cat_file(test_file_path)

    # Verify the content read is the same as the content written
    assert (
        read_content == test_content
    ), "The content read from the file does not match the content written."


def test_pipe_file(fs, remote_dir):
    test_data = b"Test data for pipe_file" * (2**20)  # 1 MB of test data
    test_file_path = remote_dir + "/test_pipe_file.txt"

    fs.pipe_file(test_file_path, test_data)

    with fs.open(test_file_path, "rb") as f:
        assert (
            f.read() == test_data
        ), "The data read from the file does not match the data written."
