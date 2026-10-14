"""Per-request context (current user, IP, user agent) used by BaseModel and the audit log."""
from contextvars import ContextVar

_ctx: ContextVar[dict] = ContextVar("request_ctx", default={})


def set_context(**values):
    return _ctx.set(values)


def reset_context(token):
    _ctx.reset(token)


def get(key, default=None):
    return _ctx.get().get(key, default)


def current_user():
    user = get("user")
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None
