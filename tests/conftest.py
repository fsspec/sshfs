import threading
from queue import Queue

import mockssh.server


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
