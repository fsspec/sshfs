# sshfs

sshfs is an implementation of [fsspec](https://github.com/intake/filesystem_spec/) for
the SFTP protocol using [asyncssh](https://github.com/ronf/asyncssh).

## Features

- A complete implementation of the fsspec protocol through SFTP
- Supports features outside of the SFTP (e.g server side copy through SSH command execution)
- Quite fast (compared to alternatives like paramiko)
- Builtin Channel Management
- Async! (thanks to `asyncssh`)

## Tutorial

Install the `sshfs` from PyPI or the conda-forge, and import it;

```py
from sshfs import SSHFileSystem
```

To connect with a password, you can simply specify `username`/`password`
as keyword arguments and connect to the host of your choosing;

```py
# Connect with a password
fs = SSHFileSystem(
    '127.0.0.1',
    username='sam',
    password='fishing'
)
```

If you want to use a private key to authenticate, you can either
pass a string pointing to the path of the key, or give a list of
them to be tried:

```py
# or with a private key
fs = SSHFileSystem(
    'ssh.example.com',
    client_keys=['/path/to/ssh/key']
)
```

All operations and their descriptions are specified [here](https://filesystem-spec.readthedocs.io/en/latest/api.html#fsspec.spec.AbstractFileSystem).
Here are a few example calls you can make, starting with `info()` which allows you to retrieve the metadata about given path;

```py
>>> details = fs.info('/tmp')
>>> print(f'{details["name"]!r} is a {details["type"]}!')
'/tmp/' is a directory!
>>>
>>> crontab = fs.info('/etc/crontab')
>>> print(f'{crontab["name"]!r} is a {crontab["type"]}!')
'/etc/crontab' is a file!
```

You can also create new files through either putting a local file with `put_file` or opening a file in write mode;

```py
>>> with fs.open('/tmp/message.dat', 'wb') as stream:
...     stream.write(b'super secret messsage!')
... 
```

And either download it through `get_file` or simply read it on the fly with opening it;

```py
>>> with fs.open('/tmp/message.dat') as stream:
...     print(stream.read())
... 
b'super secret messsage!'
```

There are also a lot of other basic filesystem operations, such as `mkdir`, `touch` and `find`;

```py
>>> fs.mkdir('/tmp/dir')
>>> fs.mkdir('/tmp/dir/eggs')
>>> fs.touch('/tmp/dir/spam')
>>> fs.touch('/tmp/dir/eggs/quux')
>>> 
>>> for file in fs.find('/tmp/dir'):
...     print(file)
... 
/tmp/dir/eggs/quux
/tmp/dir/spam
```

If you want to list a directory but not it's children, you can use `ls()`;

```py
>>> [(detail['name'], detail['type']) for detail in fs.ls('/tmp/dir', detail=True)]
[('/tmp/dir/spam', 'file'), ('/tmp/dir/eggs', 'directory')]
```
