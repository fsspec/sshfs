import getpass
from contextlib import suppress
from pathlib import Path

from asyncssh.config import SSHClientConfig

SSH_CONFIG = Path("~", ".ssh", "config").expanduser()


def parse_config(
    *, host, user=(), port=(), local_user=None, config_files=None
):
    if config_files is None:
        config_files = [SSH_CONFIG]

    if local_user is None:
        with suppress(KeyError):
            local_user = getpass.getuser()

    last_config = None
    reload = False

    return SSHClientConfig.load(
        last_config,
        config_files,
        reload,
        local_user,
        user,
        host,
        port,
    )
