import uuid

from django.conf import settings
from django.db import models

from . import context


class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        user = context.current_user()
        if user is not None:
            if self._state.adding and self.created_by_id is None:
                self.created_by = user
            self.updated_by = user
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"updated_by", "updated_at"}
        super().save(*args, **kwargs)


class VendorScopedQuerySet(models.QuerySet):
    def for_user(self, user):
        """Filter vendor-owned rows to the user's vendor. Internal users see everything.

        Postgres RLS is the backstop if a view forgets to call this.
        """
        if user.is_internal:
            return self
        vendor_id = user.vendor_id
        if vendor_id is None:
            return self.none()
        field = "id" if self.model._meta.db_table == "vendors" else "vendor_id"
        return self.filter(**{field: vendor_id})


VendorScopedManager = models.Manager.from_queryset(VendorScopedQuerySet)


class CostCentre(BaseModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=200)
    l1_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_cost_centres"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "cost_centres"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class SystemSetting(BaseModel):
    """Configurable defaults (spec: 'keep it configurable, never hard-coded')."""

    DEFAULTS = {
        "INVITE_EXPIRY_DAYS": 7,
        "PAYMENT_TERMS_DAYS": 30,
        "TOTALS_TOLERANCE": "1.00",
        "LOW_CONFIDENCE_THRESHOLD": 0.80,
    }

    key = models.CharField(max_length=64, unique=True)
    value = models.JSONField()

    class Meta:
        db_table = "system_settings"

    def __str__(self):
        return self.key

    @classmethod
    def get(cls, key):
        row = cls.objects.filter(key=key).first()
        return row.value if row else cls.DEFAULTS[key]
