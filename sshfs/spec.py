import asyncio
import posixpath
import shlex
import stat
import weakref
from contextlib import AsyncExitStack, suppress
from datetime import datetime

import asyncssh
from asyncssh.sftp import SFTPOpUnsupported
from fsspec.asyn import AsyncFileSystem, sync, sync_wrapper

from sshfs.file import SSHFile
from sshfs.pools import SFTPSoftChannelPool
from sshfs.utils import wrap_exceptions


class SSHFileSystem(AsyncFileSystem):
    def __init__(
        self,
        host,
        *,
        pool_type=SFTPSoftChannelPool,
        **kwargs,
    ):
        super().__init__(self, **kwargs)

        _client_args = kwargs.copy()
        _client_args.setdefault("known_hosts", None)

        self._stack = AsyncExitStack()
        self._client, self._pool = self.connect(
            host, pool_type, **_client_args
        )
        weakref.finalize(
            self, sync, self.loop, self._finalize, self._pool, self._stack
        )

    @wrap_exceptions
    async def _connect(self, host, pool_type, **client_args):
        _raw_client = asyncssh.connect(host, **client_args)
        client = await self._stack.enter_async_context(_raw_client)
        pool = pool_type(client)
        return client, pool

    connect = sync_wrapper(_connect)

    async def _finalize(self, pool, stack):
        await pool.close()

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
        async with self._pool.get() as channel:
            attributes = await channel.stat(path)

        info = self._decode_attributes(attributes)
        path = path.rstrip("/")
        if info["type"] == "directory":
            path += "/"
        info["name"] = path
        return info

    @wrap_exceptions
    async def _mv(self, lpath, rpath, **kwargs):
        async with self._pool.get() as channel:
            with suppress(SFTPOpUnsupported):
                return await channel.posix_rename(lpath, rpath)

        # Some systems doesn't natively support posix_rename
        # which is an extension to the original SFTP protocol.
        # In that case we are going to copy the file and delete
        # it.

        try:
            await self._cp_file(lpath, rpath)
        finally:
            await self._rm_file(lpath)

    @wrap_exceptions
    async def _put_file(self, lpath, rpath, **kwargs):
        async with self._pool.get() as channel:
            await channel.put(lpath, rpath)

    @wrap_exceptions
    async def _get_file(self, lpath, rpath, **kwargs):
        async with self._pool.get() as channel:
            await channel.get(lpath, rpath)

    @wrap_exceptions
    async def _cp_file(self, lpath, rpath, **kwargs):
        cmd = f"cp {shlex.quote(lpath)} {shlex.quote(rpath)}"
        await self.client.run(cmd, check=True)

    @wrap_exceptions
    async def _ls(self, path, detail=False, **kwargs):
        async with self._pool.get() as channel:
            file_attrs = await channel.readdir(path)

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
        async with self._pool.get() as channel:
            await channel.mkdir(path)

    @wrap_exceptions
    async def _makedirs(
        self, path, *, exist_ok=False, permissions=511, **kwargs
    ):
        attrs = asyncssh.SFTPAttrs(permissions=permissions)
        async with self._pool.get() as channel:
            await channel.makedirs(path, exist_ok=exist_ok, attrs=attrs)

    mkdir = sync_wrapper(_mkdir)
    makedirs = sync_wrapper(_makedirs)

    @wrap_exceptions
    async def _rm_file(self, path, **kwargs):
        async with self._pool.get() as channel:
            await channel.unlink(path)

    @wrap_exceptions
    async def _rmdir(
        self,
        path,
        recursive=False,
        ignore_errors=False,
        on_error=None,
        **kwargs,
    ):
        async with self._pool.get() as channel:
            if recursive:
                await channel.rmtree(
                    path, ignore_errors=ignore_errors, onerror=on_error
                )
            else:
                await channel.rmdir(path)

    async def _rm(self, path, recursive=False, **kwargs):
        if isinstance(path, str):
            path = [path]

        coros = []
        for sub_path in path:
            if await self._isdir(sub_path):
                coro = self._rmdir(sub_path, recursive, **kwargs)
            else:
                coro = self._rm_file(sub_path)
            coros.append(coro)

        await asyncio.gather(*coros)

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

    def _open(self, path, *args, **kwargs):
        return SSHFile(self, path, *args, **kwargs)
