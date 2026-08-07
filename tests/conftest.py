import asyncio
import threading
from contextlib import suppress
from pathlib import Path
from queue import Queue

import asyncssh
import mockssh.server
import pytest

_STATIC = (Path(__file__).parent / "static").resolve()
_USER_KEY = asyncssh.read_private_key(str(_STATIC / "user.key"))


class _TestSSHServer(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def public_key_auth_supported(self):
        return True

    def validate_public_key(self, username, key):
        return key == _USER_KEY.convert_to_public()


@pytest.fixture(scope="session")
def asyncssh_server(tmp_path_factory):
    """SFTP server that, unlike the paramiko-based mockssh fixture,
    implements the copy-data and limits extensions. Authenticated with
    the test user key and chrooted to a fresh directory; yields
    (host, port, root) where the remote "/" maps to root."""
    root = tmp_path_factory.mktemp("asyncssh-root")
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    async def _listen():
        return await asyncssh.listen(
            "127.0.0.1",
            0,
            server_host_keys=[_USER_KEY],
            server_factory=_TestSSHServer,
            sftp_factory=lambda chan: asyncssh.SFTPServer(
                chan, chroot=str(root)
            ),
        )

    server = asyncio.run_coroutine_threadsafe(_listen(), loop).result(30)
    try:
        yield "127.0.0.1", server.get_port(), root
    finally:

        async def _shutdown():
            server.close()
            await server.wait_closed()
            tasks = [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            ]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.wait(tasks, timeout=5)

        with suppress(Exception):
            asyncio.run_coroutine_threadsafe(_shutdown(), loop).result(30)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


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
