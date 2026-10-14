import pytest
from django.db import DatabaseError, transaction

from apps.audit import services
from apps.audit.models import AuditLog


def test_audit_log_is_append_only(db):
    row = services.log("test.event", "thing", "1", after={"x": 1})
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditLog.objects.filter(pk=row.pk).update(action="tampered")
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditLog.objects.filter(pk=row.pk).delete()
    assert AuditLog.objects.get(pk=row.pk).action == "test.event"
