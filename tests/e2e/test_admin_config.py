"""Admin configuration (docs/ARCHITECTURE.md §1 #1-2, §4 /console/admin/*, §8 test_admin_config).

- Approval-matrix edits change routing for NEW invoices only; an invoice submitted before keeps its
  snapshotted required level.
- Document checklist edits (MSME made mandatory) show on the vendor's documents page.
- Email templates can be switched off (that email is then not sent); login_otp cannot be switched off.
"""
from datetime import date
from decimal import Decimal

from django.core import mail
from playwright.sync_api import expect

from .conftest import act_on, emails_to, login, logout, upload_and_submit

VENDOR_UPLOADER = "vendora.user@test.local"
VENDOR_ADMIN = "vendora@test.local"


# --- local helpers ---------------------------------------------------------------------------

def _submit(seed, *, total, invoice_no, by_email=VENDOR_UPLOADER):
    from apps.accounts.models import User
    from apps.core.seed import sample_invoice
    from apps.invoices import services

    v = seed["vendor_a"]
    by = User.objects.get(email=by_email)
    inv = sample_invoice(v, by, total=total, invoice_no=invoice_no)
    data = {
        "supplier_gstin": v.primary_gstin.gstin, "invoice_no": invoice_no, "invoice_date": date.today(),
        "taxable": inv.taxable, "cgst": inv.cgst, "sgst": inv.sgst, "igst": inv.igst, "total": inv.total,
        "cost_centre": seed["cost_centre"],
    }
    return services.submit(inv, by=by, data=data)


def _finance_precheck(seed, inv):
    from apps.invoices import services

    return services.act(inv, by=seed["finance"], action="APPROVE", fin_data={"tds_section": "194J", "tds_amount": Decimal("0")})


def _inv(ref=None, pk=None):
    from apps.invoices.models import Invoice

    return Invoice.objects.get(portal_ref=ref) if ref else Invoice.objects.get(pk=pk)


def _csrf_post(page, url, form=None):
    token = next(c["value"] for c in page.context.cookies() if c["name"] == "csrftoken")
    return page.request.post(url, form={**(form or {}), "csrfmiddlewaretoken": token}, headers={"X-CSRFToken": token}, max_redirects=0)


# --- approval matrix -------------------------------------------------------------------------

def test_matrix_change_applies_to_new_invoices_only(page, seed, tmp_path):
    from apps.invoices.models import ApprovalMatrix

    gstin = seed["vendor_a"].primary_gstin.gstin
    old = _submit(seed, total=300000, invoice_no="MTX-OLD")          # default matrix: L1 <= 1L < 3L <= 10L -> L2
    assert old.required_level == "L2"

    login(page, "admin@test.local")
    page.goto("/console/admin/matrix/")
    expect(page.get_by_test_id("matrix-l1-0")).to_have_value("100000.00")
    page.get_by_test_id("matrix-l1-0").fill("500000")
    page.get_by_test_id("matrix-l2-0").fill("2000000")
    page.get_by_test_id("matrix-l3-0").fill("2000000")
    page.get_by_test_id("matrix-save-btn").click()
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    expect(page.get_by_test_id("matrix-l1-0")).to_have_value("500000.00")
    expect(page.get_by_test_id("matrix-l2-0")).to_have_value("2000000.00")
    m = ApprovalMatrix.objects.get(category="", cost_centre__isnull=True)
    assert (m.l1_max, m.l2_max, m.requires_l3_above) == (Decimal("500000"), Decimal("2000000"), Decimal("2000000"))
    logout(page)

    # New invoices route by the new limits.
    login(page, VENDOR_UPLOADER)
    new_ref = upload_and_submit(page, tmp_path, total=300000, gstin=gstin, invoice_no="MTX-NEW")
    big_ref = upload_and_submit(page, tmp_path, total=1500000, gstin=gstin, invoice_no="MTX-BIG")
    logout(page)
    assert _inv(new_ref).required_level == "L1"
    assert _inv(big_ref).required_level == "L2"          # was L3 under the old matrix
    assert _inv(pk=old.pk).required_level == "L2"        # snapshotted at submit, not recomputed

    # The old invoice still needs L2: after L1 approval it moves to PENDING_L2, the new one is APPROVED.
    for inv in (_inv(pk=old.pk), _inv(new_ref)):
        _finance_precheck(seed, inv)
    login(page, "l1@test.local")
    act_on(page, new_ref, "approve")
    act_on(page, old.portal_ref, "approve")
    assert _inv(new_ref).status == "APPROVED"
    assert _inv(pk=old.pk).status == "PENDING_L2"
    page.goto(f"/console/invoices/{old.pk}/")
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "PENDING_L2")


def test_matrix_rejects_l1_above_l2(page, seed):
    from apps.invoices.models import ApprovalMatrix

    login(page, "admin@test.local")
    page.goto("/console/admin/matrix/")
    page.get_by_test_id("matrix-l1-0").fill("3000000")
    page.get_by_test_id("matrix-save-btn").click()
    expect(page.get_by_test_id("flash-success")).to_have_count(0)
    m = ApprovalMatrix.objects.get(category="", cost_centre__isnull=True)
    assert m.l1_max == Decimal("100000")


def test_matrix_admin_is_admin_only(page, seed):
    login(page, "finance@test.local")
    assert page.goto("/console/admin/matrix/").status == 403


# --- document checklist ----------------------------------------------------------------------

def test_checklist_make_msme_mandatory_shows_on_vendor_documents(page, seed):
    from apps.vendors.models import DocumentType

    login(page, VENDOR_ADMIN)
    page.goto("/vendor/onboarding/documents/")
    expect(page.get_by_test_id("doc-row-MSME")).to_contain_text("Optional")
    logout(page)

    login(page, "admin@test.local")
    page.goto("/console/admin/documents/")
    row = page.get_by_test_id("doctype-row-MSME")
    # NOTE: the checkbox has no data-testid (reported); scoped to the row's testid by field name.
    mandatory = row.locator("input[type=checkbox][name$='-mandatory']")
    expect(mandatory).not_to_be_checked()
    mandatory.check()
    page.get_by_test_id("doctype-save-btn").click()
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    expect(page.get_by_test_id("doctype-row-MSME").locator("input[type=checkbox][name$='-mandatory']")).to_be_checked()
    dt = DocumentType.objects.get(code="MSME")
    assert dt.mandatory is True
    assert sorted(dt.applies_to) == ["GOODS", "PROFESSIONAL", "SERVICES"]   # other fields round-trip
    assert DocumentType.objects.get(code="PAN").mandatory is True
    logout(page)

    login(page, VENDOR_ADMIN)
    page.goto("/vendor/onboarding/documents/")
    expect(page.get_by_test_id("doc-row-MSME")).to_contain_text("Mandatory")
    expect(page.get_by_test_id("doc-row-MSME")).not_to_contain_text("Optional")


# --- email templates -------------------------------------------------------------------------

def test_template_switched_off_is_not_sent(page, seed):
    from apps.notify.models import EmailTemplate, Notification

    first = _finance_precheck(seed, _submit(seed, total=50000, invoice_no="TPL-OFF"))
    second = _finance_precheck(seed, _submit(seed, total=50000, invoice_no="TPL-ON"))

    login(page, "admin@test.local")
    page.goto("/console/admin/templates/")
    expect(page.get_by_test_id("template-status-invoice_approved")).to_contain_text("On")
    page.get_by_test_id("template-toggle-invoice_approved").click()
    expect(page.get_by_test_id("template-status-invoice_approved")).to_contain_text("Off")
    assert EmailTemplate.objects.get(code="invoice_approved").enabled is False
    logout(page)

    login(page, "l1@test.local")
    sent = len(mail.outbox)
    act_on(page, first.portal_ref, "approve")
    assert _inv(pk=first.pk).status == "APPROVED"
    assert not [m for m in mail.outbox[sent:] if "approved" in m.subject], [m.subject for m in mail.outbox[sent:]]
    assert not emails_to(VENDOR_UPLOADER, f"[{first.portal_ref}] Invoice TPL-OFF approved")
    assert Notification.objects.get(template_code="invoice_approved", channel="EMAIL", payload__portal_ref=first.portal_ref).status == "SKIPPED"
    logout(page)

    # Switched back on, the same email goes out again.
    login(page, "admin@test.local")
    page.goto("/console/admin/templates/")
    page.get_by_test_id("template-toggle-invoice_approved").click()
    expect(page.get_by_test_id("template-status-invoice_approved")).to_contain_text("On")
    logout(page)
    login(page, "l1@test.local")
    act_on(page, second.portal_ref, "approve")
    assert len(emails_to(VENDOR_UPLOADER, f"[{second.portal_ref}] Invoice TPL-ON approved")) == 1


def test_login_otp_cannot_be_switched_off(page, seed):
    from apps.notify.models import EmailTemplate

    login(page, "admin@test.local")
    page.goto("/console/admin/templates/")
    expect(page.get_by_test_id("template-row-login_otp")).to_be_visible()
    expect(page.get_by_test_id("template-toggle-login_otp")).to_have_count(0)
    expect(page.get_by_test_id("template-status-login_otp")).to_contain_text("On")
    # A crafted toggle request is refused too.
    _csrf_post(page, "/console/admin/templates/login_otp/toggle/")
    assert EmailTemplate.objects.get(code="login_otp").enabled is True
    page.goto("/console/admin/templates/")
    expect(page.get_by_test_id("flash-error")).to_be_visible()
    expect(page.get_by_test_id("template-status-login_otp")).to_contain_text("On")
    logout(page)

    # Even if the flag is forced off in the database, sign-in codes are still sent.
    EmailTemplate.objects.filter(code="login_otp").update(enabled=False)
    sent = len(mail.outbox)
    login(page, VENDOR_ADMIN)   # needs the OTP email to arrive
    assert any("sign-in code" in m.subject and VENDOR_ADMIN in m.to for m in mail.outbox[sent:])
