import asyncio
import errno
import functools
import os
import posixpath
import secrets
import shlex
import stat
import weakref
from contextlib import AsyncExitStack, suppress
from datetime import datetime

import asyncssh
from asyncssh import ProcessError
from asyncssh.misc import PermissionDenied
from asyncssh.sftp import SFTPFailure, SFTPNoSuchFile, SFTPOpUnsupported
from fsspec.asyn import AsyncFileSystem, sync, sync_wrapper
from fsspec.spec import AbstractBufferedFile

_UNSET = object()
_NOT_FOUND = os.strerror(errno.ENOENT)
_FILE_EXISTS = os.strerror(errno.EEXIST)


def _drop_unset(namespace):
    return {
        key: value for key, value in namespace.items() if value is not _UNSET
    }


def _get_tmp_file(path):
    return posixpath.join(path, f".tmp.{secrets.token_hex(16)}")


def wrap_exceptions(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except PermissionDenied as exc:
            raise PermissionError(exc.reason) from exc
        except SFTPNoSuchFile as exc:
            raise FileNotFoundError(errno.ENOENT, _NOT_FOUND)
        except ProcessError as exc:
            message = exc.stderr.strip()
            if message.endswith(_NOT_FOUND):
                raise FileNotFoundError(errno.ENOENT, _NOT_FOUND)
            raise
        except SFTPFailure as exc:
            message = exc.reason
            if message.endswith("already exists"):
                raise FileExistsError(errno.EEXIST, _FILE_EXISTS)
            raise

    return wrapper


class SSHFileSystem(AsyncFileSystem):
    def __init__(
        self,
        host,
        *,
        port=_UNSET,
        username=_UNSET,
        password=_UNSET,
        client_keys=_UNSET,
        known_hosts=_UNSET,
        **kwargs,
    ):
        super().__init__(self, **kwargs)

        self._client_args = _drop_unset(
            {
                "host": host,
                "port": port,
                "username": username,
                "password": password,
                "client_keys": client_keys,
                "known_hosts": known_hosts,
            }
        )

        self._stack = AsyncExitStack()
        self._client = self.connect()
        weakref.finalize(self, sync, self.loop, self._finalize, self._stack)

    @wrap_exceptions
    async def _connect(self):
        client = asyncssh.connect(**self._client_args)
        return await self._stack.enter_async_context(client)

    connect = sync_wrapper(_connect)

    async def _finalize(self, stack):
        # If an error occurs while the SSHFile is trying to
        # open the native file, then the client might get broken
        # due to partial initalization. We are just going to ignore
        # the errors that arises on the finalization layer
        with suppress(BrokenPipeError):
            await stack.aclose()

    @property
    def client(self):
        assert self._client is not None
        return self._client

    def _decode_attributes(self, attributes):
        if stat.S_ISDIR(attributes.permissions):
            kind = "directory"
        elif stat.S_ISREG(attributes.permissions):
            kind = "file"
        elif stat.S_ISLNK(attributes.permissions):
            kind = "link"
        else:
            kind = "unknown"

        return {
            "size": attributes.size,
            "type": kind,
            "gid": attributes.gid,
            "uid": attributes.uid,
            "time": datetime.utcfromtimestamp(attributes.atime),
            "mtime": datetime.utcfromtimestamp(attributes.mtime),
            "permissions": attributes.permissions,
        }

    @wrap_exceptions
    async def _info(self, path, **kwargs):
        async with self.client.start_sftp_client() as sftp:
            attributes = await sftp.stat(path)

        info = self._decode_attributes(attributes)
        info["name"] = path
        return info

    @wrap_exceptions
    async def _mv(self, lpath, rpath, **kwargs):
        async with self.client.start_sftp_client() as sftp:
            try:
                await sftp.posix_rename(lpath, rpath)
            except SFTPOpUnsupported:
                # Some systems doesn't natively support posix_rename
                # which is an extension to the original SFTP protocol.
                # In that case we are going to copy the file and delete
                # it.

                try:
                    await self._cp_file(lpath, rpath)
                finally:
                    await self._rm_file(lpath)

    @wrap_exceptions
    async def _cp_file(self, lpath, rpath, **kwargs):
        cmd = f"cp {shlex.quote(lpath)} {shlex.quote(rpath)}"
        await self.client.run(cmd, check=True)

    @wrap_exceptions
    async def _ls(self, path, detail=False, **kwargs):
        async with self.client.start_sftp_client() as sftp:
            file_attrs = await sftp.readdir(path)

        infos = []
        for file_attr in file_attrs:
            if file_attr.filename in ["", ".", ".."]:
                continue
            info = self._decode_attributes(file_attr.attrs)
            info["name"] = posixpath.join(path, file_attr.filename)
            infos.append(info)

        # TODO: listings cache
        if detail:
            return infos
        else:
            return [info["name"] for info in infos]

    @wrap_exceptions
    async def _mkdir(
        self, path, *, create_parents=True, permissions=511, **kwargs
    ):
        if create_parents:
            return await self._makedirs(path, exist_ok=True)

        attrs = asyncssh.SFTPAttrs(permissions=permissions)
        async with self.client.start_sftp_client() as sftp:
            await sftp.mkdir(path)

    @wrap_exceptions
    async def _makedirs(
        self, path, *, exist_ok=False, permissions=511, **kwargs
    ):
        attrs = asyncssh.SFTPAttrs(permissions=permissions)
        async with self.client.start_sftp_client() as sftp:
            await sftp.makedirs(path, exist_ok=exist_ok, attrs=attrs)

    makedirs = sync_wrapper(_makedirs)

    @wrap_exceptions
    async def _rm_file(self, path, **kwargs):
        async with self.client.start_sftp_client() as sftp:
            await sftp.unlink(path)

    @wrap_exceptions
    async def _rmdir(self, path, **kwargs):
        async with self.client.start_sftp_client() as sftp:
            await sftp.rmdir(path)

    @wrap_exceptions
    async def _checksum(self, path):
        system = await self._get_system()
        if system == "Linux":
            command = "md5sum"
            part = 0
        elif system == "Darwin":
            command = "md5"
            part = -1
        else:
            raise ValueError(f"{system!r} doesn't support checksum operation")

        cmd = f"{command} {shlex.quote(path)}"
        result = await self.client.run(cmd, check=True)

        parts = result.stdout.strip().split()
        assert len(parts) >= 1

        checksum = parts[part]
        assert len(checksum) == 32
        return checksum

    @wrap_exceptions
    async def _get_system(self):
        result = await self.client.run("uname", check=True)
        return result.stdout.strip()

    checksum = sync_wrapper(_checksum)
    get_system = sync_wrapper(_get_system)

    def _open(self, path, mode="rb", **kwargs):
        return SSHFile(self, path, mode, **kwargs)


class SSHFile(AbstractBufferedFile):
    def __init__(self, fs, path, mode="rb", **kwargs):

        super().__init__(fs, path, mode, **kwargs)
        self.loop = self.fs.loop

        if self.mode not in {"rb", "wb"}:
            raise ValueError(f"Unsupported file open mode: {self.mode!r}")

        if "w" in self.mode:
            self._location = _get_tmp_file(self.fs._parent(self.path))
        else:
            self._location = self.path

        self._stack = AsyncExitStack()
        try:
            self._file = self._open_file()
        except Exception:
            self.closed = True
            self.close()
            raise

    @wrap_exceptions
    async def _async_open_file(self):
        sftp = await self._stack.enter_async_context(
            self.fs.client.start_sftp_client()
        )
        # TODO: maybe pass block_size?
        return await self._stack.enter_async_context(
            sftp.open(self._location, self.mode)
        )

    _open_file = sync_wrapper(_async_open_file)

    async def _async_fetch_range(self, start, end):
        await self._file.seek(start)
        return await self._file.read(end - start)

    _fetch_range = sync_wrapper(_async_fetch_range)

    async def _async_upload_chunk(self, final=False):
        await self._file.write(self.buffer.getvalue())
        if self.autocommit and final:
            await self._commit()

    _upload_chunk = sync_wrapper(_async_upload_chunk)

    async def _commit(self):
        if "w" not in self.mode:
            return None

        await self.fs._mv(self._location, self.path)

    commit = sync_wrapper(_commit)

    def close(self):
        super().close()
        sync(self.loop, self._stack.aclose)
