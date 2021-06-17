__all__ = ["AsyncExitStack", "asynccontextmanager"]

try:
    from contextlib import AsyncExitStack
except ImportError:
    from async_exit_stack import AsyncExitStack

try:
    from contextlib import asynccontextmanager
except ImportError:
    # Unfortunately there isn't any backport of asynccontextmanager,
    # so we have to implement a fairly basic version for our needs until
    # we drop support for 3.6
    # See: https://github.com/python/cpython/blob/fee96422e6f0056561cf74fef2012cc066c9db86/Lib/contextlib.py#L164-L207
    from functools import wraps

    class _AsyncGeneratorContextManager:
        def __init__(self, func, args, kwargs):
            self.gen = func(*args, **kwargs)

        async def __aenter__(self):
            try:
                return await self.gen.__anext__()
            except StopAsyncIteration:
                raise RuntimeError from None

        async def __aexit__(self, typ, value, traceback):
            if typ is None:
                try:
                    await self.gen.__anext__()
                except StopAsyncIteration:
                    return
                else:
                    raise RuntimeError("generator didn't stop")
            else:
                if value is None:
                    value = typ()
                try:
                    await self.gen.athrow(typ, value, traceback)
                    raise RuntimeError("generator didn't stop after athrow()")
                except StopAsyncIteration as exc:
                    return exc is not value
                except RuntimeError as exc:
                    if exc is value:
                        return False
                    if isinstance(value, (StopIteration, StopAsyncIteration)):
                        if exc.__cause__ is value:
                            return False
                    raise
                except BaseException as exc:
                    if exc is not value:
                        raise

    def asynccontextmanager(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return _AsyncGeneratorContextManager(func, args, kwargs)

        return wrapper
