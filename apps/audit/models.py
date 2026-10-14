import uuid

from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Append-only. A DB trigger rejects UPDATE and DELETE (migration 0002)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.DO_NOTHING, db_constraint=False, related_name="+")
    entity = models.CharField(max_length=64)
    entity_id = models.CharField(max_length=64, blank=True)
    action = models.CharField(max_length=64)
    before_json = models.JSONField(null=True, blank=True)
    after_json = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)
    at = models.DateTimeField(auto_now_add=True)
    # spec: every table carries these columns
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.DO_NOTHING, db_constraint=False, related_name="+")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.DO_NOTHING, db_constraint=False, related_name="+")

    class Meta:
        db_table = "audit_log"
        ordering = ["-at"]
        indexes = [models.Index(fields=["entity", "entity_id"]), models.Index(fields=["at"])]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} {self.entity}:{self.entity_id}"
