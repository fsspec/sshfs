import errno
import functools
import os

from asyncssh import ProcessError
from asyncssh.misc import PermissionDenied
from asyncssh.sftp import SFTPFailure, SFTPNoSuchFile
from fsspec.asyn import sync_wrapper

_NOT_FOUND = os.strerror(errno.ENOENT)
_FILE_EXISTS = os.strerror(errno.EEXIST)

# A copy of SFTP_BLOCK_SIZE (16KB)
BASE_BLOCK_SIZE = 2 ** 14

# Most of the SFTP implementations support reading a 64kb
# and writing a 256kb chunk at a single request. We'll set
# these values with a 16kb margin.
READ_BLOCK_SIZE = BASE_BLOCK_SIZE * 3
WRITE_BLOCK_SIZE = BASE_BLOCK_SIZE * 15


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


def as_progress_handler(callback):
    if callback is None:
        return None

    sent_total = False

    def progress_handler(src_path, dst_path, absolute_progress, total_size):
        nonlocal sent_total
        if not sent_total:
            callback.set_size(total_size)
            sent_total = True

        callback.absolute_update(absolute_progress)

    return progress_handler
