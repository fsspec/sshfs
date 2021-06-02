import asyncio
from contextlib import AsyncExitStack, asynccontextmanager

import pytest

_POLL_WAIT = 0.1


@asynccontextmanager
async def open_channels(pool, amount):
    stack = AsyncExitStack()
    try:
        channels = [
            await stack.enter_async_context(pool.get()) for _ in range(amount)
        ]
        yield channels
    finally:
        await stack.aclose()


async def open_channel(pool):
    async with pool.get() as channel:
        return channel


class FakeSFTPClient:
    def __init__(self, no):
        self.no = no


class FakeSSHClient:
    def __init__(self, max_channels=None):
        self.counter = 0
        self.max_channels = max_channels

    @asynccontextmanager
    async def start_sftp_client(self):
        from asyncssh.misc import ChannelOpenError

        if self.max_channels is not None and self.counter == self.max_channels:
            raise ChannelOpenError(None, None)

        self.counter += 1
        yield FakeSFTPClient(self.counter)


@pytest.fixture
def fake_client():
    yield FakeSSHClient()


@pytest.mark.asyncio
async def test_pool_hard_queue_caching(fake_client):
    from sshfs import SFTPHardChannelPool

    pool = SFTPHardChannelPool(fake_client, poll=False)

    async with pool.get() as channel:
        assert channel.no == 1
        assert pool.active_channels == 1

    assert pool.active_channels == 0

    # Ensure that it won't create a new channel
    # but use the cached one since it is now free.
    async with pool.get() as channel:
        assert channel.no == 1

    async with open_channels(pool, 5) as channels:
        assert {channel.no for channel in channels} == {1, 2, 3, 4, 5}
        assert pool.active_channels == 5


@pytest.mark.asyncio
async def test_pool_hard_queue_limits(fake_client):
    from sshfs import SFTPHardChannelPool

    pool = SFTPHardChannelPool(fake_client, poll=False, max_channels=2)

    async with open_channels(pool, 2) as channels:
        with pytest.raises(asyncio.QueueEmpty):
            await open_channel(pool)


@pytest.mark.asyncio
async def test_pool_hard_queue_auto_limitting(fake_client):
    from sshfs import SFTPHardChannelPool

    fake_client.max_channels = 4
    pool = SFTPHardChannelPool(fake_client, poll=False)

    async with open_channels(pool, 4) as channels:
        with pytest.raises(asyncio.QueueEmpty):
            await open_channel(pool)

        assert pool.active_channels == fake_client.max_channels
        assert pool.max_channels == fake_client.max_channels
