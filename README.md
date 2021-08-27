# sshfs

sshfs is an implementation of [fsspec](https://github.com/intake/filesystem_spec/) for
the SFTP protocol using [asyncssh](https://github.com/ronf/asyncssh).

## Features
- A complete implementation of the fsspec protocol through SFTP
- Supports features outside of the SFTP (e.g server side copy through SSH command execution)
- Quite fast (compared to alternatives like paramiko)
- Builtin Channel Management
- Async! (thanks to `asyncssh`)
