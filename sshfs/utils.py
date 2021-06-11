import errno
import functools
import os

from asyncssh import ProcessError
from asyncssh.misc import PermissionDenied
from asyncssh.sftp import SFTPFailure, SFTPNoSuchFile
from fsspec.asyn import sync_wrapper

_NOT_FOUND = os.strerror(errno.ENOENT)
_FILE_EXISTS = os.strerror(errno.EEXIST)


def wrap_exceptions(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except PermissionDenied as exc:
            raise PermissionError(exc.reason) from exc
        except SFTPNoSuchFile as exc:
            raise FileNotFoundError(errno.ENOENT, _NOT_FOUND) from exc
        except ProcessError as exc:
            message = exc.stderr.strip()
            if message.endswith(_NOT_FOUND):
                raise FileNotFoundError(errno.ENOENT, _NOT_FOUND) from exc
            raise
        except SFTPFailure as exc:
            message = exc.reason
            if message.endswith("already exists"):
                raise FileExistsError(errno.EEXIST, _FILE_EXISTS) from exc
            raise

    return wrapper


def _mirror_method(method):
    async def _method(self, *args, **kwargs):
        wrapped_meth = getattr(self._file, method)
        return await wrapped_meth(*args, **kwargs)

    _method.__name__ = method
    return sync_wrapper(_method)
