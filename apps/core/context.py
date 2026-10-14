"""Per-request context (current user, IP, user agent) used by BaseModel and the audit log."""
from contextvars import ContextVar

_ctx: ContextVar[dict | None] = ContextVar("request_ctx", default=None)


def set_context(**values):
    return _ctx.set(values)


def reset_context(token):
    _ctx.reset(token)


def get(key, default=None):
    return (_ctx.get() or {}).get(key, default)


def current_user():
    user = get("user")
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


def acting_as(fn):
    """Service decorator: the `by=` user becomes the current user (audit actor, created_by) when no
    request context exists — e.g. Celery tasks, management commands and tests."""
    from functools import wraps

    @wraps(fn)
    def wrapped(*args, **kwargs):
        by = kwargs.get("by")
        if by is None or current_user() is not None:
            return fn(*args, **kwargs)
        token = _ctx.set({**(_ctx.get() or {}), "user": by})
        try:
            return fn(*args, **kwargs)
        finally:
            _ctx.reset(token)

    return wrapped
