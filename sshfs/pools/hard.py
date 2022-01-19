import asyncio
from contextlib import asynccontextmanager

from sshfs.pools.base import BaseSFTPChannelPool


class SFTPHardChannelPool(BaseSFTPChannelPool):
    """A SFTP pool implementation that ensures at any moment in time,
    a single SFTP channel might only be used by a single coroutine. When there
    are no more active channels, the ``.get()`` method will block for a channel
    to get released (if ``timeout`` is specified, it will poll for ``timeout``
    seconds until a ``TimeoutError`` is raised)."""

    def __init__(self, *args, **kwargs):
        self._queue = asyncio.Queue(0)
        self._poll = kwargs.pop("poll", True)
        self.active_channels = 0
        super().__init__(*args, **kwargs)

    @asynccontextmanager
    async def get(self):
        channel = None
        if self._queue.empty():
            channel = await self._maybe_new_channel()

        if channel is None:
            if self._queue.qsize() == 0 and not self.active_channels:
                raise ValueError("Can't create any SFTP connections!")

            if self._poll:
                channel = await asyncio.wait_for(
                    self._queue.get(), timeout=self.timeout
                )
            else:
                channel = self._queue.get_nowait()

        self.active_channels += 1
        try:
            yield channel
        finally:
            self.active_channels -= 1
            self._queue.put_nowait(channel)

    async def _cleanup(self):
        while not self._queue.empty():
            self._queue.get_nowait()
