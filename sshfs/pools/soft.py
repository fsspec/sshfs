import heapq
from collections import Counter
from contextlib import asynccontextmanager

from sshfs.pools.base import BaseSFTPChannelPool


class SFTPSoftChannelPool(BaseSFTPChannelPool):
    """A SFTP pool implementation that allows usage of same channels
    by multiple coroutines and handles the balanced distribution of multiple
    channels from least used to most used. The ``.get()`` method will not block
    unlike the hard pool and no timeouts will happen on the management side."""

    # Placeholder to use when there are no channels in
    # the counter.
    _NO_CHANNELS = [[None, 1]]

    def __init__(self, *args, **kwargs):
        self._channels = Counter()
        super().__init__(*args, **kwargs)

    @asynccontextmanager
    async def get(self):
        [(least_used_channel, num_connections)] = (
            heapq.nsmallest(1, self._channels.items(), lambda kv: kv[1])
            or self._NO_CHANNELS
        )

        if num_connections > 0:
            channel = await self._maybe_new_channel()
            if channel is not None:
                least_used_channel = channel
                num_connections = 0

        if least_used_channel is None:
            raise ValueError("Can't create any SFTP connections!")

        self._channels[least_used_channel] += 1
        self.active_channels += 1
        yield least_used_channel
        self._channels[least_used_channel] -= 1
        self.active_channels -= 1

    async def _cleanup(self):
        self._channels.clear()
