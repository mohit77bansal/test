from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class EmailTemplate(BaseModel):
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    subject = models.CharField(max_length=300)
    enabled = models.BooleanField(default=True)
    system = models.BooleanField(default=False, help_text="Security emails (OTP, reset) cannot be switched off")

    class Meta:
        db_table = "email_templates"
        ordering = ["code"]

    def __str__(self):
        return self.code


class Notification(BaseModel):
    EMAIL = "EMAIL"
    IN_APP = "IN_APP"
    CHANNELS = [(EMAIL, "Email"), (IN_APP, "In-app")]
    QUEUED, SENT, FAILED, SKIPPED = "QUEUED", "SENT", "FAILED", "SKIPPED"
    STATUSES = [(s, s.title()) for s in (QUEUED, SENT, FAILED, SKIPPED)]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications")
    to_emails = models.JSONField(default=list, blank=True)
    cc_emails = models.JSONField(default=list, blank=True)
    template_code = models.CharField(max_length=64)
    subject = models.CharField(max_length=300, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    channel = models.CharField(max_length=10, choices=CHANNELS)
    status = models.CharField(max_length=10, choices=STATUSES, default=QUEUED)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    response = models.TextField(blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    link = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "read_at"])]

    def __str__(self):
        return f"{self.channel} {self.template_code} {self.status}"
