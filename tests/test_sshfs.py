import hashlib
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


def test_move(fs, remote_dir):
    fs.touch(remote_dir + "/a.txt")
    initial_info = fs.info(remote_dir + "/a.txt")

    fs.move(remote_dir + "/a.txt", remote_dir + "/b.txt")
    secondary_info = fs.info(remote_dir + "/b.txt")

    assert not fs.exists(remote_dir + "/a.txt")
    assert fs.exists(remote_dir + "/b.txt")

    initial_info.pop("name")
    secondary_info.pop("name")
    assert initial_info == secondary_info


def test_copy(fs, remote_dir):
    fs.touch(remote_dir + "/a.txt")
    initial_info = fs.info(remote_dir + "/a.txt")

    fs.copy(remote_dir + "/a.txt", remote_dir + "/b.txt")
    secondary_info = fs.info(remote_dir + "/b.txt")

    assert fs.exists(remote_dir + "/a.txt")
    assert fs.exists(remote_dir + "/b.txt")

    initial_info.pop("name")
    secondary_info.pop("name")
    assert initial_info == secondary_info


def test_rm(fs, remote_dir):
    fs.touch(remote_dir + "/a.txt")
    fs.rm(remote_dir + "/a.txt")
    assert not fs.exists(remote_dir + "/a.txt")

    fs.mkdir(remote_dir + "/dir")
    fs.rm(remote_dir + "/dir")
    assert not fs.exists(remote_dir + "/dir")

    fs.mkdir(remote_dir + "/dir")
    fs.touch(remote_dir + "/dir/a")
    fs.touch(remote_dir + "/dir/b")
    fs.mkdir(remote_dir + "/dir/c/")
    fs.touch(remote_dir + "/dir/c/a/")
    fs.rm(remote_dir + "/dir", recursive=True)
    assert not fs.exists(remote_dir + "/dir")


def test_checksum(fs, remote_dir):
    data = b"iterative.ai"

    with fs.open(remote_dir + "/a.txt", "wb") as stream:
        stream.write(data)

    checksum = hashlib.md5(data).hexdigest()
    assert fs.checksum(remote_dir + "/a.txt") == checksum
