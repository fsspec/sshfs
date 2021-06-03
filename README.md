# sshfs

sshfs is a filesystem interface for SSH/SFTP. It is based on top
of [asyncssh](https://github.com/ronf/asyncssh) and implements
the [fsspec](https://github.com/intake/filesystem_spec/) protocol.

## Features
- Supports filesystem operations outside of SFTP, e.g server side copy
- Auto SFTP channel management
- Async! (thanks to `asyncssh`)

## Example
```py
from sshfs import SSHFileSystem


# Connect with a password
fs = SSHFileSystem(
    '127.0.0.1',
    username='sam',
    password='fishing'
)

# or with a private key
fs = SSHFileSystem(
    'ssh.example.com',
    client_keys=['/path/to/ssh/key']
)

details = fs.info('/tmp')
print(f'{details['name']} is a {details['type']}!')


with fs.open('/tmp/message.dat', 'wb') as stream:
    stream.write(b'super secret messsage!')

with fs.open('/tmp/message.dat') as stream:
    print(stream.read())


fs.mkdir('/tmp/dir')
fs.mkdir('/tmp/dir/eggs')
fs.touch('/tmp/dir/spam')
fs.touch('/tmp/dir/eggs/quux')

for file in fs.find('/tmp/dir'):
    print(file)
```
