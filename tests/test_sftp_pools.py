import asyncio
from contextlib import AsyncExitStack, asynccontextmanager

import pytest

from sshfs.pools import SFTPHardChannelPool, SFTPSoftChannelPool


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


def all_queues(func):
    return pytest.mark.parametrize(
        "queue_type", [SFTPHardChannelPool, SFTPSoftChannelPool]
    )(func)


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

        if self.max_channels is not None and self.counter >= self.max_channels:
            raise ChannelOpenError(None, None)

        self.counter += 1
        yield FakeSFTPClient(self.counter)


@pytest.fixture
def fake_client():
    yield FakeSSHClient()


@pytest.mark.asyncio
async def test_pool_soft_queue_caching(fake_client):
    pool = SFTPSoftChannelPool(fake_client, poll=False)

    async with pool.get() as channel:
        assert channel.no == 1
        assert pool.active_channels == 1

    assert pool.active_channels == 1

    # Ensure that it won't create a new channel
    # but use the cached one since it is now free.
    async with pool.get() as channel:
        assert channel.no == 1

    assert fake_client.counter == 1

    async with open_channels(pool, 4) as channels:
        assert {channel.no for channel in channels} == {1}
        assert pool.active_channels == 1

    async with open_channels(pool, 8) as channels:
        assert {channel.no for channel in channels} == {1, 2}
        assert pool.active_channels == 2

    async with open_channels(pool, 12) as channels:
        assert {channel.no for channel in channels} == {1, 2, 3}
        assert pool.active_channels == 3


@pytest.mark.asyncio
@all_queues
async def test_pool_general_no_connections(fake_client, queue_type):
    pool = queue_type(fake_client, poll=False)
    fake_client.max_channels = 0

    with pytest.raises(ValueError):
        await open_channel(pool)


@pytest.mark.asyncio
async def test_pool_hard_queue_limits(fake_client):
    pool = SFTPHardChannelPool(fake_client, poll=False, max_channels=2)

    async with open_channels(pool, 2) as channels:
        with pytest.raises(asyncio.QueueEmpty):
            await open_channel(pool)


@pytest.mark.asyncio
async def test_pool_hard_queue_auto_limitting(fake_client):
    fake_client.max_channels = 4
    pool = SFTPHardChannelPool(fake_client, poll=False)

    async with open_channels(pool, 4) as channels:
        with pytest.raises(asyncio.QueueEmpty):
            await open_channel(pool)

        assert pool.active_channels == fake_client.max_channels
        assert pool.max_channels == fake_client.max_channels


@pytest.mark.asyncio
async def test_pool_soft_queue_limits(fake_client):
    pool = SFTPSoftChannelPool(fake_client, max_channels=2)

    async with open_channels(pool, 6) as channels:
        assert len({channel.no for channel in channels}) <= 2


@pytest.mark.asyncio
async def test_pool_soft_queue_balancing(fake_client):
    pool = SFTPSoftChannelPool(fake_client, max_channels=4)

    async with pool.get() as channel_1:
        assert channel_1.no == 1

    async with pool.get() as channel_1:
        assert channel_1.no == 1

    async with open_channels(pool, 18) as channels:

        primaries, secondaries = channels[:16], channels[16:]
        assert {channel.no for channel in primaries} == {1, 2, 3, 4}
        assert {channel.no for channel in secondaries} == {1, 2}

        async with pool.get() as channel_3:
            assert channel_3.no == 3

        async with pool.get() as channel_3:
            async with pool.get() as channel_4:
                assert channel_4.no == 4
            assert channel_3.no == 3
