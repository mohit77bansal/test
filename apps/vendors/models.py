import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core import crypto
from apps.core.models import BaseModel, VendorScopedManager

CATEGORIES = [("GOODS", "Goods"), ("SERVICES", "Services"), ("PROFESSIONAL", "Professional")]


class Vendor(BaseModel):
    INVITED, DRAFT, SUBMITTED, UNDER_REVIEW, ACTIVE, REJECTED, BLOCKED = (
        "INVITED", "DRAFT", "SUBMITTED", "UNDER_REVIEW", "ACTIVE", "REJECTED", "BLOCKED",
    )
    STATUSES = [(s, s.replace("_", " ").title()) for s in (INVITED, DRAFT, SUBMITTED, UNDER_REVIEW, ACTIVE, REJECTED, BLOCKED)]
    CONSTITUTIONS = [
        ("PRIVATE_LTD", "Private Limited"), ("PUBLIC_LTD", "Public Limited"), ("LLP", "LLP"),
        ("PARTNERSHIP", "Partnership"), ("PROPRIETORSHIP", "Proprietorship"), ("OTHER", "Other"),
    ]
    MSME_STATUSES = [("NOT_MSME", "Not MSME"), ("MSME", "MSME registered")]
    MSME_TYPES = [("", "—"), ("MICRO", "Micro"), ("SMALL", "Small"), ("MEDIUM", "Medium")]

    vendor_code = models.CharField(max_length=16, unique=True)
    legal_name = models.CharField(max_length=250)
    trade_name = models.CharField(max_length=250, blank=True)
    category = models.CharField(max_length=20, choices=CATEGORIES)
    pan_enc = models.TextField(blank=True)
    pan_last4 = models.CharField(max_length=4, blank=True)
    constitution = models.CharField(max_length=20, choices=CONSTITUTIONS, blank=True)
    msme_status = models.CharField(max_length=10, choices=MSME_STATUSES, blank=True)
    udyam_no = models.CharField(max_length=32, blank=True)
    msme_type = models.CharField(max_length=10, choices=MSME_TYPES, blank=True)
    contact_name = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    status = models.CharField(max_length=15, choices=STATUSES, default=INVITED)
    owner_manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="managed_vendors")
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="invited_vendors")
    activated_at = models.DateTimeField(null=True, blank=True)
    block_reason = models.TextField(blank=True)
    review_remarks = models.TextField(blank=True)
    consent_at = models.DateTimeField(null=True, blank=True)

    objects = VendorScopedManager()

    class Meta:
        db_table = "vendors"
        ordering = ["legal_name"]
        indexes = [models.Index(fields=["status"])]

    def __str__(self):
        return f"{self.vendor_code} {self.legal_name}"

    @property
    def pan(self):
        return crypto.decrypt(self.pan_enc)

    @pan.setter
    def pan(self, value):
        value = (value or "").strip().upper()
        self.pan_enc = crypto.encrypt(value)
        self.pan_last4 = value[-4:]

    @property
    def primary_gstin(self):
        return self.gstins.filter(is_primary=True).first()

    @property
    def active_bank_account(self):
        return self.bank_accounts.filter(status=VendorBankAccount.ACTIVE).first()

    @property
    def has_pending_bank_change(self):
        return self.status == self.ACTIVE and self.bank_accounts.filter(status=VendorBankAccount.PENDING).exists()

    def mandatory_document_types(self):
        return DocumentType.for_category(self.category).filter(mandatory=True)


class VendorInvite(BaseModel):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="invites")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    email = models.EmailField()
    name = models.CharField(max_length=200, blank=True)
    is_admin = models.BooleanField(default=True)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "vendor_invites"

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @property
    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now()


class VendorUser(BaseModel):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="vendor_users")
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vendor_link")
    is_admin = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    objects = VendorScopedManager()

    class Meta:
        db_table = "vendor_users"

    def __str__(self):
        return f"{self.user.email} @ {self.vendor.vendor_code}"


class VendorGSTIN(BaseModel):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="gstins")
    gstin = models.CharField(max_length=15)
    state_code = models.CharField(max_length=2)
    address = models.TextField(blank=True)
    is_primary = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)

    objects = VendorScopedManager()

    class Meta:
        db_table = "vendor_gstins"
        constraints = [models.UniqueConstraint(fields=["vendor", "gstin"], name="uniq_vendor_gstin")]

    def __str__(self):
        return self.gstin


class VendorBankAccount(BaseModel):
    PENDING, ACTIVE, REJECTED, SUPERSEDED = "PENDING", "ACTIVE", "REJECTED", "SUPERSEDED"
    STATUSES = [(s, s.title()) for s in (PENDING, ACTIVE, REJECTED, SUPERSEDED)]

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="bank_accounts")
    holder_name = models.CharField(max_length=200)
    account_no_enc = models.TextField()
    account_last4 = models.CharField(max_length=4)
    ifsc = models.CharField(max_length=11)
    bank_name = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=PENDING)
    version = models.PositiveIntegerField(default=1)
    verified_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decision_remarks = models.TextField(blank=True)

    objects = VendorScopedManager()

    class Meta:
        db_table = "vendor_bank_accounts"
        indexes = [models.Index(fields=["vendor", "status"])]

    def __str__(self):
        return f"{self.bank_name} {crypto.mask(self.account_last4)}"

    @property
    def account_no(self):
        return crypto.decrypt(self.account_no_enc)

    @account_no.setter
    def account_no(self, value):
        value = (value or "").strip()
        self.account_no_enc = crypto.encrypt(value)
        self.account_last4 = value[-4:]

    @property
    def masked(self):
        return crypto.mask(self.account_last4)


class DocumentType(BaseModel):
    PARSER_PROFILES = [("NONE", "None"), ("PAN", "PAN"), ("GST", "GST"), ("CHEQUE", "Cheque"), ("UDYAM", "Udyam")]

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=200)
    applies_to = models.JSONField(default=list, help_text="Vendor categories, e.g. [\"GOODS\", \"SERVICES\"]")
    mandatory = models.BooleanField(default=True)
    has_expiry = models.BooleanField(default=False)
    parser_profile = models.CharField(max_length=10, choices=PARSER_PROFILES, default="NONE")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "document_types"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

    @classmethod
    def for_category(cls, category):
        return cls.objects.filter(is_active=True, applies_to__contains=[category])


class VendorDocument(BaseModel):
    UPLOADED, APPROVED, REJECTED = "UPLOADED", "APPROVED", "REJECTED"
    STATUSES = [(s, s.title()) for s in (UPLOADED, APPROVED, REJECTED)]

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="documents")
    document_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT)
    file = models.ForeignKey("parsing.File", on_delete=models.PROTECT)
    parse_job = models.ForeignKey("parsing.ParseJob", null=True, blank=True, on_delete=models.SET_NULL)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=UPLOADED)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    remarks = models.TextField(blank=True)
    is_current = models.BooleanField(default=True)

    objects = VendorScopedManager()

    class Meta:
        db_table = "vendor_documents"
        indexes = [models.Index(fields=["vendor", "document_type"])]

    def __str__(self):
        return f"{self.vendor.vendor_code} {self.document_type.code}"
