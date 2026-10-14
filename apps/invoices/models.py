from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum

from apps.core.models import BaseModel, VendorScopedManager
from apps.vendors.models import CATEGORIES

MONEY = {"max_digits": 14, "decimal_places": 2}


class Level(models.TextChoices):
    FIN_PRE = "FIN_PRE", "Finance pre-check"
    L1 = "L1", "L1"
    L2 = "L2", "L2"
    L3 = "L3", "L3"


LEVEL_ORDER = [Level.FIN_PRE, Level.L1, Level.L2, Level.L3]


class ApprovalMatrix(BaseModel):
    """Amount bands per level. Most specific row wins: (category + cost centre) > cost centre > category > default."""

    category = models.CharField(max_length=20, choices=CATEGORIES, blank=True)
    cost_centre = models.ForeignKey("core.CostCentre", null=True, blank=True, on_delete=models.CASCADE)
    l1_max = models.DecimalField(**MONEY, help_text="Invoices up to this total stop at L1")
    l2_max = models.DecimalField(**MONEY, help_text="Invoices up to this total stop at L2")
    requires_l3_above = models.DecimalField(**MONEY, help_text="Invoices above this total need L3 (normally = L2 max)")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "approval_matrix"
        verbose_name_plural = "approval matrix"

    def __str__(self):
        scope = " / ".join(filter(None, [self.category, self.cost_centre.code if self.cost_centre_id else ""])) or "Default"
        return f"{scope}: L1 ≤ {self.l1_max}, L2 ≤ {self.l2_max}"

    @classmethod
    def resolve(cls, category, cost_centre):
        rows = cls.objects.filter(is_active=True).filter(Q(category="") | Q(category=category)).filter(
            Q(cost_centre__isnull=True) | Q(cost_centre=cost_centre)
        )
        best = sorted(rows, key=lambda r: (r.cost_centre_id is not None, r.category != ""), reverse=True)
        return best[0] if best else None

    def required_level(self, total: Decimal) -> str:
        if total <= self.l1_max:
            return Level.L1
        if total <= self.l2_max and total <= self.requires_l3_above:
            return Level.L2
        return Level.L3


class PurchaseOrder(BaseModel):
    """Phase 2 only — not used in the MVP (all invoices are non-PO)."""

    vendor = models.ForeignKey("vendors.Vendor", on_delete=models.PROTECT, related_name="purchase_orders")
    po_no = models.CharField(max_length=50)
    cost_centre = models.ForeignKey("core.CostCentre", on_delete=models.PROTECT)
    amount = models.DecimalField(**MONEY)
    consumed_amount = models.DecimalField(**MONEY, default=0)
    valid_till = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, default="OPEN")

    objects = VendorScopedManager()

    class Meta:
        db_table = "purchase_orders"


class Invoice(BaseModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        FIN_PRECHECK = "FIN_PRECHECK", "Finance pre-check"
        PENDING_L1 = "PENDING_L1", "Pending L1"
        PENDING_L2 = "PENDING_L2", "Pending L2"
        PENDING_L3 = "PENDING_L3", "Pending L3"
        QUERY_RAISED = "QUERY_RAISED", "Query raised"
        APPROVED = "APPROVED", "Approved"
        PARTLY_PAID = "PARTLY_PAID", "Partly paid"
        PAID = "PAID", "Paid"
        REJECTED = "REJECTED", "Rejected"

    TDS_SECTIONS = [("", "—"), ("194C", "194C Contractors"), ("194J", "194J Professional / technical"), ("194I", "194I Rent"), ("194H", "194H Commission"), ("194Q", "194Q Purchase of goods"), ("NA", "Not applicable")]

    vendor = models.ForeignKey("vendors.Vendor", on_delete=models.PROTECT, related_name="invoices")
    gstin = models.ForeignKey("vendors.VendorGSTIN", null=True, blank=True, on_delete=models.PROTECT)
    portal_ref = models.CharField(max_length=20, unique=True, null=True, blank=True)
    invoice_no = models.CharField(max_length=50, blank=True)
    invoice_date = models.DateField(null=True, blank=True)
    fy = models.CharField(max_length=7, blank=True)
    po = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT)
    cost_centre = models.ForeignKey("core.CostCentre", null=True, blank=True, on_delete=models.PROTECT)
    taxable = models.DecimalField(**MONEY, null=True, blank=True)
    cgst = models.DecimalField(**MONEY, default=0)
    sgst = models.DecimalField(**MONEY, default=0)
    igst = models.DecimalField(**MONEY, default=0)
    total = models.DecimalField(**MONEY, null=True, blank=True)
    tds_section = models.CharField(max_length=10, choices=TDS_SECTIONS, blank=True)
    tds_amount = models.DecimalField(**MONEY, default=0)
    file = models.ForeignKey("parsing.File", on_delete=models.PROTECT)
    parse_job = models.ForeignKey("parsing.ParseJob", null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.DRAFT)
    current_level = models.CharField(max_length=8, choices=Level.choices, blank=True)
    required_level = models.CharField(max_length=8, choices=Level.choices, blank=True)
    query_level = models.CharField(max_length=8, choices=Level.choices, blank=True)
    due_date = models.DateField(null=True, blank=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = VendorScopedManager()

    class Meta:
        db_table = "invoices"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "invoice_no", "fy"],
                condition=~Q(status__in=["REJECTED", "DRAFT"]),
                name="uniq_vendor_invoice_fy",
            )
        ]
        indexes = [models.Index(fields=["status", "current_level"])]

    def __str__(self):
        return self.portal_ref or f"Draft {self.invoice_no}"

    @property
    def gst_total(self):
        return (self.cgst or 0) + (self.sgst or 0) + (self.igst or 0)

    @property
    def paid_amount(self):
        return self.payments.aggregate(s=Sum("amount"))["s"] or Decimal("0")

    @property
    def tds_deducted(self):
        return self.payments.aggregate(s=Sum("tds_deducted"))["s"] or Decimal("0")

    @property
    def outstanding(self):
        if self.total is None:
            return Decimal("0")
        return self.total - self.paid_amount - self.tds_deducted

    @property
    def is_open(self):
        return self.status in (self.Status.FIN_PRECHECK, self.Status.PENDING_L1, self.Status.PENDING_L2, self.Status.PENDING_L3)


class InvoiceLineItem(BaseModel):
    vendor = models.ForeignKey("vendors.Vendor", on_delete=models.PROTECT, related_name="+")
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="line_items")
    description = models.CharField(max_length=500, blank=True)
    hsn_sac = models.CharField(max_length=10, blank=True)
    qty = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    rate = models.DecimalField(**MONEY, null=True, blank=True)
    amount = models.DecimalField(**MONEY, null=True, blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    objects = VendorScopedManager()

    class Meta:
        db_table = "invoice_line_items"


class ApprovalAction(BaseModel):
    class Action(models.TextChoices):
        SUBMIT = "SUBMIT", "Submitted"
        APPROVE = "APPROVE", "Approved"
        REJECT = "REJECT", "Rejected"
        QUERY = "QUERY", "Query raised"
        REPLY = "REPLY", "Vendor replied"

    vendor = models.ForeignKey("vendors.Vendor", on_delete=models.PROTECT, related_name="+")
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="actions")
    level = models.CharField(max_length=8)  # FIN_PRE / L1 / L2 / L3 / VENDOR
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=10, choices=Action.choices)
    comment = models.TextField(blank=True)
    acted_at = models.DateTimeField(auto_now_add=True)

    objects = VendorScopedManager()

    class Meta:
        db_table = "approval_actions"
        ordering = ["acted_at"]
        indexes = [models.Index(fields=["invoice"])]


class InvoiceMessage(BaseModel):
    vendor = models.ForeignKey("vendors.Vendor", on_delete=models.PROTECT, related_name="+")
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="messages")
    from_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    body = models.TextField()
    file = models.ForeignKey("parsing.File", null=True, blank=True, on_delete=models.PROTECT)

    objects = VendorScopedManager()

    class Meta:
        db_table = "invoice_messages"
        ordering = ["created_at"]


class Payment(BaseModel):
    vendor = models.ForeignKey("vendors.Vendor", on_delete=models.PROTECT, related_name="payments")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(**MONEY)
    paid_on = models.DateField()
    utr = models.CharField(max_length=40)
    tds_deducted = models.DecimalField(**MONEY, default=0)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    objects = VendorScopedManager()

    class Meta:
        db_table = "payments"
        ordering = ["-paid_on", "-created_at"]
