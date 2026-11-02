"""Transport-neutral call identity propagated into execution workers."""
from contextlib import contextmanager
from contextvars import ContextVar

_call = ContextVar("tool_call_identity", default=None)


def current_call():
    return dict(_call.get() or {})


@contextmanager
def call_context(call_id, batch_id=""):
    token = _call.set({"call_id": call_id, "batch_id": batch_id})
    try:
        yield
    finally:
        _call.reset(token)
