import getpass
from contextlib import suppress
from pathlib import Path, PurePath
from typing import Sequence, Union
from asyncssh.config import SSHClientConfig

SSH_CONFIG = Path("~", ".ssh", "config").expanduser()
FilePath = Union[str, PurePath]
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
    config = SSHClientConfig(
        last_config =last_config,
        reload = reload,
        canonical = False,
        final=False,
        local_user = local_user,
        user = user,
        host = host,
        port = port,
    )

    if config_files:
        if isinstance(config_files, (str, PurePath)):
            paths: Sequence[FilePath] = [config_files]
        else:
            paths = config_files

        for path in paths:
            config.parse(Path(path))
        config.loaded = True
    return config
