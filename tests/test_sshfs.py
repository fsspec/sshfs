import pytest

from sshfs import SSHFileSystem

_STATIC = Path(__file__).parent / "static"

USERS = {"test": _STATIC / "user.key"}


@pytest.fixture
def ssh_server():
    import mockssh

    users = {TEST_SSH_USER: TEST_SSH_KEY_PATH}
    with mockssh.Server(users) as server:
        yield server
