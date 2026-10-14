import datetime
import decimal
import uuid

from django.db import models

from apps.core import context

from .models import AuditLog

REDACT = {"password", "account_no_enc", "pan_enc", "code_hash", "token_hash"}


def _jsonable(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def snapshot(instance: models.Model, fields=None) -> dict:
    data = {}
    for f in instance._meta.concrete_fields:
        if fields and f.name not in fields:
            continue
        if f.name in REDACT or f.name in {"created_at", "updated_at", "created_by", "updated_by"}:
            continue
        data[f.name] = _jsonable(getattr(instance, f.attname))
    return data


def diff(before: dict, after: dict):
    keys = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}


def log(action, entity, entity_id="", before=None, after=None, actor=None):
    if actor is None:
        actor = context.current_user()
    return AuditLog.objects.create(
        actor=actor,
        created_by=actor,
        updated_by=actor,
        entity=entity,
        entity_id=str(entity_id or ""),
        action=action,
        before_json=_jsonable(before) if before is not None else None,
        after_json=_jsonable(after) if after is not None else None,
        ip=context.get("ip"),
        user_agent=(context.get("user_agent") or "")[:400],
    )
