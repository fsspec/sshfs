import pytest
from test_sshfs import fs, plain_remote_dir, remote_dir, ssh_server


def test_rmdir_removes_directory(fs, remote_dir):
    path = remote_dir + "/empty"
    fs.mkdir(path)
    assert fs.isdir(path)

    fs.rmdir(path)
    assert not fs.exists(path)

    with pytest.raises(FileNotFoundError):
        fs.rmdir(path)

    fs.makedirs(path + "/child")
    fs.touch(path + "/child/file")
    fs.rmdir(path, recursive=True)
    assert not fs.exists(path)
