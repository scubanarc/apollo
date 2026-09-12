"""Operation-scoped settings, connections and caches; no cross-request state."""

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from apollo import settings


@dataclass
class Operation:
    stack: ExitStack
    cache: dict[str, Any] = field(default_factory=dict)


_current: ContextVar[Operation] = ContextVar("apollo_operation")


def operation() -> Operation:
    return _current.get()


@contextmanager
def scope(config: settings.Settings):
    with ExitStack() as stack:
        token = settings._current.set(config)
        resource_token = _current.set(Operation(stack))
        try:
            yield
        finally:
            _current.reset(resource_token)
            settings._current.reset(token)
