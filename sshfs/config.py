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

    return SSHClientConfig.load(
        reload=False,
        last_config=None,
        config_paths=config_files,
        local_user=local_user,
        user=user,
        host=host,
        port=port,
    )
