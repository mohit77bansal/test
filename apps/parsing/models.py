from django.conf import settings
from django.db import models

from apps.core.models import BaseModel, VendorScopedManager


class File(BaseModel):
    PENDING, CLEAN, INFECTED, SKIPPED = "PENDING", "CLEAN", "INFECTED", "SKIPPED"
    SCAN_STATUSES = [(s, s.title()) for s in (PENDING, CLEAN, INFECTED, SKIPPED)]

    vendor = models.ForeignKey("vendors.Vendor", null=True, blank=True, on_delete=models.PROTECT, related_name="files")
    s3_key = models.CharField(max_length=500)
    bucket = models.CharField(max_length=100)
    original_name = models.CharField(max_length=255)
    mime = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    scan_status = models.CharField(max_length=10, choices=SCAN_STATUSES, default=PENDING)

    objects = VendorScopedManager()

    class Meta:
        db_table = "files"
        indexes = [models.Index(fields=["vendor", "sha256"])]

    def __str__(self):
        return self.original_name

    @property
    def is_viewable(self):
        return self.scan_status in (self.CLEAN, self.SKIPPED)


class ParseJob(BaseModel):
    INVOICE, PAN, GST, CHEQUE, UDYAM = "INVOICE", "PAN", "GST", "CHEQUE", "UDYAM"
    KINDS = [(k, k.title()) for k in (INVOICE, PAN, GST, CHEQUE, UDYAM)]
    QUEUED, RUNNING, DONE, FAILED = "QUEUED", "RUNNING", "DONE", "FAILED"
    STATUSES = [(s, s.title()) for s in (QUEUED, RUNNING, DONE, FAILED)]

    vendor = models.ForeignKey("vendors.Vendor", null=True, blank=True, on_delete=models.PROTECT, related_name="parse_jobs")
    file = models.ForeignKey(File, on_delete=models.PROTECT, related_name="parse_jobs")
    kind = models.CharField(max_length=10, choices=KINDS)
    engine = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=QUEUED)
    raw_json = models.JSONField(null=True, blank=True)
    extracted_json = models.JSONField(null=True, blank=True)
    confidence = models.JSONField(default=dict, blank=True)
    checks = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    objects = VendorScopedManager()

    class Meta:
        db_table = "parse_jobs"

    def __str__(self):
        return f"{self.kind} {self.status}"

    @property
    def finished(self):
        return self.status in (self.DONE, self.FAILED)

    def low_confidence_fields(self, threshold=None):
        from apps.core.models import SystemSetting

        threshold = threshold if threshold is not None else float(SystemSetting.get("LOW_CONFIDENCE_THRESHOLD"))
        return {k for k, v in (self.confidence or {}).items() if v is not None and float(v) < threshold}
