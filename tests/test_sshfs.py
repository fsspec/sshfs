import posixpath
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sshfs import SSHFileSystem

_STATIC = (Path(__file__).parent / "static").resolve()
USERS = {"user": _STATIC / "user.key"}

DEVIATION = 5


@pytest.fixture(scope="session")
def ssh_server():
    import mockssh

    with mockssh.Server(USERS) as server:
        yield server


@pytest.fixture(scope="session")
def base_remote_dir():
    with tempfile.TemporaryDirectory() as path:
        yield path


@pytest.fixture
def remote_dir(fs, base_remote_dir, request):
    directory = posixpath.join(base_remote_dir, request.node.name).rstrip("/")
    fs.mkdir(directory)
    yield directory


@pytest.fixture
def fs(ssh_server, user="user"):
    yield SSHFileSystem(
        host=ssh_server.host,
        port=ssh_server.port,
        username=user,
        client_keys=[USERS[user]],
    )


def test_info(fs, remote_dir):
    fs.touch(remote_dir + "/a.txt")
    details = fs.info(remote_dir + "/a.txt")
    assert details["type"] == "file"
    assert details["name"] == remote_dir + "/a.txt"
    assert details["size"] == 0

    fs.mkdir(remote_dir + "/dir")
    details = fs.info(remote_dir + "/dir")
    assert details["type"] == "directory"
    assert details["name"] == remote_dir + "/dir/"

    details = fs.info(remote_dir + "/dir/")
    assert details["name"] == remote_dir + "/dir/"
