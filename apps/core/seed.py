"""Seed data shared by `manage.py seed` and the test fixtures (docs/ARCHITECTURE.md §8)."""
from datetime import date
from decimal import Decimal

from django.utils import timezone

from apps.core.permissions import ALL_ROLES, ROLE_NAMES

DOCUMENT_TYPES = [
    # code, name, applies_to, mandatory, has_expiry, parser_profile, order
    ("PAN", "PAN card", ["GOODS", "SERVICES", "PROFESSIONAL"], True, False, "PAN", 1),
    ("GST_CERT", "GST registration certificate", ["GOODS", "SERVICES", "PROFESSIONAL"], True, False, "GST", 2),
    ("CANCELLED_CHEQUE", "Cancelled cheque", ["GOODS", "SERVICES", "PROFESSIONAL"], True, False, "CHEQUE", 3),
    ("MSME", "MSME / Udyam certificate", ["GOODS", "SERVICES", "PROFESSIONAL"], False, True, "UDYAM", 4),
    ("AGREEMENT", "Signed agreement", ["SERVICES", "PROFESSIONAL"], True, True, "NONE", 5),
]

EMAIL_TEMPLATES = [
    # code, name, subject, system
    ("login_otp", "Login OTP", "Your Solfin Vendor Portal sign-in code: {{ code }}", True),
    ("password_reset", "Password reset", "Reset your Solfin Vendor Portal password", True),
    ("vendor_invited", "Vendor invited", "Invitation to register on the Solfin Vendor Portal", False),
    ("vendor_user_invited", "Vendor user added", "You have been added to {{ vendor_name }} on the Solfin Vendor Portal", False),
    ("registration_submitted", "Registration submitted", "[{{ vendor_code }}] New vendor awaiting KYC review: {{ vendor_name }}", False),
    ("kyc_sent_back", "KYC sent back", "[{{ vendor_code }}] Action needed on your registration", False),
    ("vendor_activated", "Vendor activated", "[{{ vendor_code }}] Welcome to the Solfin Vendor Portal", False),
    ("vendor_rejected", "Vendor rejected", "[{{ vendor_code }}] Your registration was not approved", False),
    ("vendor_blocked", "Vendor blocked", "[{{ vendor_code }}] Your vendor account has been blocked", False),
    ("bank_change_requested", "Bank details change requested", "[{{ vendor_code }}] Bank details change awaiting verification", False),
    ("bank_change_decided", "Bank details change decided", "[{{ vendor_code }}] Bank details change {{ decision }}", False),
    ("invoice_submitted", "Invoice submitted", "[{{ vendor_code }}] Invoice {{ invoice_no }} received — {{ portal_ref }}", False),
    ("approval_pending", "Approval pending", "[{{ portal_ref }}] Invoice awaiting your {{ level_label }}", False),
    ("query_raised", "Query raised", "[{{ portal_ref }}] Query on invoice {{ invoice_no }}", False),
    ("vendor_replied", "Vendor replied", "[{{ portal_ref }}] Vendor replied to your query", False),
    ("invoice_approved", "Invoice approved", "[{{ portal_ref }}] Invoice {{ invoice_no }} approved", False),
    ("invoice_rejected", "Invoice rejected", "[{{ portal_ref }}] Invoice {{ invoice_no }} rejected", False),
    ("payment_recorded", "Payment recorded", "[{{ portal_ref }}] Payment advice for invoice {{ invoice_no }}", False),
    ("daily_digest", "Daily digest", "Vendor Portal: {{ count }} item(s) pending your action", False),
]

DEFAULT_PASSWORD = "Passw0rd!234"
INTERNAL_USERS = [
    # email, name, roles
    ("vm@test.local", "Vera Manager", ["VENDOR_MANAGER"]),
    ("vm2@test.local", "Victor Manager", ["VENDOR_MANAGER"]),
    ("l1@test.local", "Lina L1", ["L1"]),
    ("l2@test.local", "Leo L2", ["L2"]),
    ("l3@test.local", "Lara L3", ["L3"]),
    ("finance@test.local", "Farah Finance", ["FINANCE"]),
    ("finance2@test.local", "Felix Finance", ["FINANCE"]),
    ("admin@test.local", "Adam Admin", ["ADMIN"]),
    ("auditor@test.local", "Asha Auditor", ["AUDITOR"]),
]


def seed_reference():
    from apps.accounts.models import Role
    from apps.core.models import CostCentre
    from apps.invoices.models import ApprovalMatrix
    from apps.notify.models import EmailTemplate
    from apps.vendors.models import DocumentType

    for code in ALL_ROLES:
        Role.objects.update_or_create(code=code, defaults={"name": ROLE_NAMES[code]})
    for code, name, applies, mandatory, expiry, profile, order in DOCUMENT_TYPES:
        DocumentType.objects.update_or_create(
            code=code,
            defaults=dict(name=name, applies_to=applies, mandatory=mandatory, has_expiry=expiry, parser_profile=profile, sort_order=order),
        )
    for code, name, subject, system in EMAIL_TEMPLATES:
        EmailTemplate.objects.update_or_create(code=code, defaults=dict(name=name, subject=subject, system=system))
    cc, _ = CostCentre.objects.get_or_create(code="CC-OPS", defaults={"name": "Operations"})
    if not ApprovalMatrix.objects.filter(category="", cost_centre__isnull=True).exists():
        ApprovalMatrix.objects.create(l1_max=Decimal("100000"), l2_max=Decimal("1000000"), requires_l3_above=Decimal("1000000"))
    return cc


def _user(email, name, user_type, roles=(), cost_centre=None):
    from apps.accounts.models import Role, User, UserRole

    user = User.objects.filter(email=email).first()
    if user is None:
        user = User.objects.create_user(email, DEFAULT_PASSWORD, name=name, user_type=user_type)
    for code in roles:
        UserRole.objects.get_or_create(user=user, role=Role.objects.get(code=code), cost_centre=cost_centre)
    return user


def _vendor(code, legal_name, email, state, pan, admin_name):
    from apps.parsing.validators import make_gstin
    from apps.vendors.models import Vendor, VendorBankAccount, VendorGSTIN, VendorUser

    vendor = Vendor.objects.filter(vendor_code=code).first()
    if vendor:
        return vendor
    vm = _user("vm@test.local", "Vera Manager", "INTERNAL", ["VENDOR_MANAGER"])
    vendor = Vendor(
        vendor_code=code, legal_name=legal_name, trade_name=legal_name, category="SERVICES", constitution="PRIVATE_LTD",
        msme_status="NOT_MSME", contact_name=admin_name, contact_email=email, contact_phone="9800000000",
        address=f"1 Business Park, State {state}", status=Vendor.ACTIVE, owner_manager=vm, invited_by=vm,
        activated_at=timezone.now(), consent_at=timezone.now(),
    )
    vendor.pan = pan
    vendor.save()
    VendorGSTIN.objects.create(vendor=vendor, gstin=make_gstin(state, pan), state_code=state, is_primary=True, verified_at=timezone.now())
    acct = VendorBankAccount(vendor=vendor, holder_name=legal_name, ifsc="HDFC0001234", bank_name="HDFC Bank", status=VendorBankAccount.ACTIVE, verified_at=timezone.now())
    acct.account_no = "5010012345" + code[-2:]
    acct.save()
    admin = _user(email, admin_name, "VENDOR", ["VENDOR_ADMIN"])
    VendorUser.objects.create(vendor=vendor, user=admin, is_admin=True)
    return vendor


def seed_demo():
    """Fixed users per role, vendors A and B (active), sample invoices at ₹50,000 and ₹15,00,000."""
    from apps.core.models import CostCentre

    cc = seed_reference()
    users = {}
    for email, name, roles in INTERNAL_USERS:
        users[email.split("@")[0]] = _user(email, name, "INTERNAL", roles)
    if cc.l1_owner_id is None:
        cc.l1_owner = users["l1"]
        cc.save()
    users["vendor_a"] = None
    vendor_a = _vendor("V00001", "Acme Supplies Private Limited", "vendora@test.local", "27", "AAACA1234A", "Arjun Acme")
    vendor_b = _vendor("V00002", "Bharat Traders Private Limited", "vendorb@test.local", "29", "AAACB5678B", "Bina Bharat")
    a_user = _user("vendora.user@test.local", "Anil Accounts", "VENDOR", ["VENDOR_USER"])
    from apps.vendors.models import VendorUser

    VendorUser.objects.get_or_create(user=a_user, defaults={"vendor": vendor_a, "is_admin": False})
    return {"vendor_a": vendor_a, "vendor_b": vendor_b, "cost_centre": CostCentre.objects.get(code="CC-OPS"), **users}


def sample_invoice(vendor, user, *, total, invoice_no, status="DRAFT", cost_centre=None):
    """Create a draft invoice with a stored dummy PDF (used by fixtures and demo seed)."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.core.models import CostCentre
    from apps.invoices.models import Invoice
    from apps.parsing.services import store_upload
    from apps.parsing.validators import financial_year

    total = Decimal(total)
    taxable = (total / Decimal("1.18")).quantize(Decimal("0.01"))
    gst = total - taxable
    half = (gst / 2).quantize(Decimal("0.01"))
    pdf = SimpleUploadedFile(f"{invoice_no}.pdf", b"%PDF-1.4 sample " + invoice_no.encode(), content_type="application/pdf")
    f = store_upload(pdf, vendor=vendor, user=user)
    today = date.today()
    return Invoice.objects.create(
        vendor=vendor, gstin=vendor.primary_gstin, invoice_no=invoice_no, invoice_date=today, fy=financial_year(today),
        cost_centre=cost_centre or CostCentre.objects.get(code="CC-OPS"), taxable=taxable, cgst=half, sgst=gst - half,
        igst=0, total=total, file=f, status=status,
    )
