import io

from asyncssh.sftp import _MAX_SFTP_REQUESTS
from fsspec.asyn import sync

from sshfs.utils import _mirror_method, wrap_exceptions

# A copy of SFTP_BLOCK_SIZE (16KB)
BASE_BLOCK_SIZE = 2 ** 14


class SSHFile(io.IOBase):
    def __init__(
        self, fs, path, mode="rb", block_size=None, max_requests=None, **kwargs
    ):
        self.fs = fs
        self.loop = fs.loop

        # TODO: support r+ / w+ / a+
        if mode not in {"rb", "wb", "ab"}:
            raise ValueError("Unsupported file mode: {mode}")

        self.path = path
        self.mode = mode
        self.max_requests = max_requests or _MAX_SFTP_REQUESTS

        if block_size is None:
            # "The OpenSSH SFTP server will close the connection
            # if it receives a message larger than 256 KB, and
            # limits read requests to returning no more than
            # 64 KB."
            #
            # We are going to use the maximum block_size possible
            # with a 16KB margin (so instead of sending 256 KB data,
            # we'll send 240 KB + headers for write requests)

            if self.readable():
                block_size = BASE_BLOCK_SIZE * 3
            else:
                block_size = BASE_BLOCK_SIZE * 15

        # The blocksize is often used with constructs like
        # shutil.copyfileobj(src, dst, length=file.blocksize) and since we are
        # using pipelining, we are going to reflect the total size rather than
        # a size of chunk to our limits.
        self.blocksize = block_size * self.max_requests

        self.kwargs = kwargs

        self._file = sync(self.loop, self._open_file)
        self._closed = False

    @wrap_exceptions
    async def _open_file(self):
        # TODO: this needs to keep a reference to the
        # pool as well, otherwise we might broke our
        # guarantee for the hard pool since the file
        # will still be using that channel to perform
        # it's operations but the pool it thinking this
        # channel is freed.
        async with self.fs._pool.get() as channel:
            return await channel.open(
                self.path,
                self.mode,
                block_size=self.blocksize // self.max_requests,
                max_requests=self.max_requests,
            )

    read = _mirror_method("read")
    seek = _mirror_method("seek")
    tell = _mirror_method("tell")

    write = _mirror_method("write")
    fsync = _mirror_method("fsync")
    truncate = _mirror_method("truncate")

    _close = _mirror_method("close")

    def readable(self):
        return "r" in self.mode

    def writable(self):
        return not self.readable()

    def close(self):
        if self._closed:
            return None

        self._close()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    @property
    def closed(self):
        return self._closed
