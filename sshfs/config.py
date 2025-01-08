import getpass
import warnings
from contextlib import suppress
from pathlib import Path

import asyncssh
from asyncssh.config import SSHClientConfig

SSH_CONFIG = Path("~", ".ssh", "config").expanduser()


def parse_config(
    *, host, user=(), port=(), local_user=None, config_files=None
):
    warnings.warn(
        "parse_config is deprecated and will be removed in the future.",
        DeprecationWarning,
    )

    if config_files is None:
        config_files = [SSH_CONFIG]

    if local_user is None:
        with suppress(KeyError, OSError):
            local_user = getpass.getuser()

    last_config = None
    reload = False

    version = tuple(map(int, asyncssh.__version__.split(".")))
    if version < (2, 19, 0):
        return SSHClientConfig.load(
            last_config,
            config_files,
            reload,
            local_user,
            user,
            host,
            port,
        )
    canonical = False
    final = False
    return SSHClientConfig.load(
        last_config,
        config_files,
        reload,
        canonical,
        final,
        local_user,
        user,
        host,
        port,
    )
