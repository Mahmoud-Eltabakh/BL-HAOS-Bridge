"""DEBUG-only, value-free call spans for independently deployed runtimes."""

from asyncio import CancelledError
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
from itertools import count
import logging
from time import perf_counter
from typing import TypeVar, cast

_Function = TypeVar("_Function", bound=Callable[..., object])
_current_span: ContextVar[int | None] = ContextVar("bl_haos_call_span", default=None)
_spans = count(1)


@contextmanager
def trace_scope(logger: logging.Logger, name: str) -> Generator[None, None, None]:
    """Observe a call without logging values, exception text, or tracebacks."""
    if not logger.isEnabledFor(logging.DEBUG):
        yield
        return
    span = next(_spans)
    parent = _current_span.get()
    token = _current_span.set(span)
    started = perf_counter()
    prefix = "[BL-HAOS] " if logger.name.startswith("custom_components.") else ""
    fields = (prefix, name, span, parent if parent is not None else "none")
    logger.debug("%scall enter function=%s span=%s parent=%s", *fields)
    try:
        yield
    except (CancelledError, GeneratorExit):
        logger.debug(
            "%scall cancel function=%s span=%s parent=%s elapsed_ms=%.3f",
            *fields, (perf_counter() - started) * 1000,
        )
        raise
    except BaseException as error:
        logger.debug(
            "%scall error function=%s span=%s parent=%s elapsed_ms=%.3f error_type=%s",
            *fields, (perf_counter() - started) * 1000, type(error).__name__,
        )
        raise
    else:
        logger.debug(
            "%scall exit function=%s span=%s parent=%s elapsed_ms=%.3f",
            *fields, (perf_counter() - started) * 1000,
        )
    finally:
        _current_span.reset(token)


def trace_call(function: _Function) -> _Function:
    """Preserve callable metadata and coroutine behavior while tracing calls."""
    logger = logging.getLogger(function.__module__)
    name = f"{function.__module__}.{function.__qualname__}"
    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def async_call(*args: object, **kwargs: object) -> object:
            if not logger.isEnabledFor(logging.DEBUG):
                return await function(*args, **kwargs)
            with trace_scope(logger, name):
                return await function(*args, **kwargs)

        return cast(_Function, async_call)

    @wraps(function)
    def sync_call(*args: object, **kwargs: object) -> object:
        if not logger.isEnabledFor(logging.DEBUG):
            return function(*args, **kwargs)
        with trace_scope(logger, name):
            return function(*args, **kwargs)

    return cast(_Function, sync_call)
