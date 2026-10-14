import hashlib
import secrets
from datetime import timedelta
from functools import cached_property

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)

    def create_user(self, email, password=None, **extra):
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True, user_type=User.INTERNAL)
        return self.create_user(email, password, **extra)


class User(BaseModel, AbstractBaseUser, PermissionsMixin):
    VENDOR = "VENDOR"
    INTERNAL = "INTERNAL"
    USER_TYPES = [(VENDOR, "Vendor"), (INTERNAL, "Internal")]
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    STATUSES = [(ACTIVE, "Active"), (INACTIVE, "Inactive")]

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, blank=True)
    user_type = models.CharField(max_length=10, choices=USER_TYPES)
    status = models.CharField(max_length=10, choices=STATUSES, default=ACTIVE)
    mfa_enabled = models.BooleanField(default=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    is_staff = models.BooleanField(default=False)  # Django admin (/sysadmin/) only

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        db_table = "users"
        ordering = ["email"]

    def __str__(self):
        return f"{self.name} <{self.email}>"

    @property
    def is_active(self):
        return self.status == self.ACTIVE

    @property
    def is_internal(self):
        return self.user_type == self.INTERNAL

    @cached_property
    def role_codes(self) -> set[str]:
        return set(self.user_roles.values_list("role__code", flat=True))

    def has_role(self, code):
        return code in self.role_codes

    @cached_property
    def vendor_membership(self):
        if self.is_internal:
            return None
        from apps.core.rls import internal_context
        from apps.vendors.models import VendorUser

        with internal_context():
            return VendorUser.objects.select_related("vendor").filter(user=self, is_active=True).first()

    @property
    def vendor_id(self):
        m = self.vendor_membership
        return m.vendor_id if m else None

    @property
    def vendor(self):
        m = self.vendor_membership
        return m.vendor if m else None


class Role(BaseModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "roles"
        ordering = ["code"]

    def __str__(self):
        return self.name


class UserRole(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_roles")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="user_roles")
    cost_centre = models.ForeignKey(
        "core.CostCentre", null=True, blank=True, on_delete=models.CASCADE, help_text="L1 scope; blank = all"
    )

    class Meta:
        db_table = "user_roles"
        constraints = [
            models.UniqueConstraint(fields=["user", "role", "cost_centre"], name="uniq_user_role_scope"),
            models.UniqueConstraint(
                fields=["user", "role"], condition=models.Q(cost_centre__isnull=True), name="uniq_user_role_unscoped"
            ),
        ]

    def __str__(self):
        scope = f" ({self.cost_centre.code})" if self.cost_centre_id else ""
        return f"{self.user.email}: {self.role.code}{scope}"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class LoginOTP(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="otps")
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "login_otps"

    @classmethod
    def issue(cls, user):
        cls.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
        code = f"{secrets.randbelow(10**6):06d}"
        otp = cls.objects.create(
            user=user, code_hash=_hash(code), expires_at=timezone.now() + timedelta(seconds=settings.OTP_TTL_SECONDS)
        )
        return otp, code

    def verify(self, code: str) -> bool:
        if self.used_at or self.expires_at < timezone.now() or self.attempts >= settings.OTP_MAX_ATTEMPTS:
            return False
        self.attempts += 1
        ok = secrets.compare_digest(self.code_hash, _hash(code.strip()))
        if ok:
            self.used_at = timezone.now()
        self.save(update_fields=["attempts", "used_at"])
        return ok

    @property
    def exhausted(self):
        return self.attempts >= settings.OTP_MAX_ATTEMPTS
