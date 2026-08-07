import asyncio
import threading
from pathlib import Path
from queue import Queue

import asyncssh
import mockssh.server
import pytest

_STATIC = (Path(__file__).parent / "static").resolve()


class _NoAuthSSHServer(asyncssh.SSHServer):
    def begin_auth(self, username):
        return False


@pytest.fixture(scope="session")
def asyncssh_server():
    """SFTP server that, unlike the paramiko-based mockssh fixture,
    implements the copy-data and limits extensions. Serves the local
    filesystem as the current user; yields (host, port)."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    async def _listen():
        return await asyncssh.listen(
            "127.0.0.1",
            0,
            server_host_keys=[str(_STATIC / "user.key")],
            server_factory=_NoAuthSSHServer,
            sftp_factory=asyncssh.SFTPServer,
        )

    server = asyncio.run_coroutine_threadsafe(_listen(), loop).result(30)
    try:
        yield "127.0.0.1", server.get_port()
    finally:
        loop.call_soon_threadsafe(server.close)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)


def _handler_run(self):
    # Identical to mockssh.server.Handler.run except that the command
    # queue is created atomically. Upstream checks `chanid not in
    # command_queues` and then assigns a fresh Queue, while paramiko's
    # transport thread may concurrently create the queue and put the
    # command into it via check_channel_exec_request(); the assignment
    # then replaces that queue, the command is lost, and handle_client
    # blocks on Queue.get() forever -- the client never receives an
    # exit status. mock-ssh-server is unmaintained, so it is patched
    # here instead of upstream.
    self.transport.start_server(server=self)
    while True:
        channel = self.transport.accept()
        if channel is None:
            break
        self.command_queues.setdefault(channel.chanid, Queue())
        thread = threading.Thread(target=self.handle_client, args=(channel,))
        thread.daemon = True
        thread.start()


mockssh.server.Handler.run = _handler_run
