import hashlib
import posixpath
import secrets
import tempfile
import warnings
from concurrent import futures
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from asyncssh.sftp import SFTPFailure

from sshfs import SSHFileSystem

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


def test_move(fs, remote_dir):
    fs.touch(remote_dir + "/a.txt")
    initial_info = fs.info(remote_dir + "/a.txt")

    fs.move(remote_dir + "/a.txt", remote_dir + "/b.txt")
    secondary_info = fs.info(remote_dir + "/b.txt")

    assert not fs.exists(remote_dir + "/a.txt")
    assert fs.exists(remote_dir + "/b.txt")

    initial_info.pop("name")
    secondary_info.pop("name")
    assert initial_info == secondary_info


def test_copy(fs, remote_dir):
    fs.touch(remote_dir + "/a.txt")
    initial_info = fs.info(remote_dir + "/a.txt")

    fs.copy(remote_dir + "/a.txt", remote_dir + "/b.txt")
    secondary_info = fs.info(remote_dir + "/b.txt")

    assert fs.exists(remote_dir + "/a.txt")
    assert fs.exists(remote_dir + "/b.txt")

    assert strip_keys(initial_info) == strip_keys(secondary_info)


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

    assert set(fs.ls(remote_dir + "dir/")) == files

    dirs = fs.ls(remote_dir + "dir/", detail=True)
    expected = [fs.info(file) for file in files]

    by_name = lambda details: details["name"]
    dirs.sort(key=by_name)
    expected.sort(key=by_name)

    assert dirs == expected


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
