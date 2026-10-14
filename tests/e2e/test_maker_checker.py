"""Maker-checker: no self-approval, no repeat approval at a later level, Finance pre-checker cannot approve
at L1-L3, and a pending bank-detail change holds payments until Finance approves it."""
import json
from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from playwright.sync_api import expect

from .conftest import act_on, emails_to, invoice_fields, login, logout, upload_and_submit


def _invoice(seed, total, invoice_no, *, to=None):
    """Submit an invoice for vendor A via the services and walk it through the given approvals."""
    from apps.accounts.models import User
    from apps.core.models import CostCentre
    from apps.invoices import services

    vendor = seed["vendor_a"]
    by = User.objects.get(email="vendora@test.local")
    fields = invoice_fields(total, vendor.primary_gstin.gstin, invoice_no)
    pdf = SimpleUploadedFile(f"{invoice_no}.pdf", b"%PDF-1.4\nFAKE_PARSE:" + json.dumps(fields).encode() + b":END\n%%EOF", content_type="application/pdf")
    inv = services.create_from_upload(vendor, by=by, uploaded=pdf)
    data = {k: (Decimal(v) if k in ("taxable", "cgst", "sgst", "igst", "total") else v) for k, v in fields.items()}
    data.update(invoice_date=date.today(), cost_centre=CostCentre.objects.get(code="CC-OPS"))
    inv = services.submit(inv, by=by, data=data)
    for actor in to or []:
        inv = services.act(inv, by=User.objects.get(email=actor), action="APPROVE", fin_data={"tds_section": "194J", "tds_amount": Decimal("0")})
    inv.refresh_from_db()
    return inv


def _post(page, path, data=None):
    token = next(c["value"] for c in page.context.cookies() if c["name"] == "csrftoken")
    return page.request.post(path, form=data or {}, headers={"X-CSRFToken": token, "Referer": page.url}, max_redirects=0)


def _status(inv):
    inv.refresh_from_db()
    return inv.status


def test_uploader_cannot_approve_vendor_has_no_console(page, seed, tmp_path):
    login(page, "vendora@test.local")
    ref = upload_and_submit(page, tmp_path, total=50000, gstin=seed["vendor_a"].primary_gstin.gstin, invoice_no="MC-SELF-1")
    from apps.invoices.models import Invoice

    inv = Invoice.objects.get(portal_ref=ref)
    for path in ("/console/", f"/console/invoices/{inv.pk}/", "/console/invoices/"):
        resp = page.goto(path)
        assert resp.status == 404, (path, resp.status)
        expect(page.get_by_test_id("invoice-approve-btn")).to_have_count(0)
    # A forged approval POST is refused too.
    page.goto("/vendor/")
    resp = _post(page, f"/console/invoices/{inv.pk}/", {"action": "APPROVE", "tds_section": "194J"})
    assert resp.status == 404
    assert _status(inv) == "FIN_PRECHECK"
    assert not inv.actions.filter(action="APPROVE").exists()


def test_l1_approver_with_l2_role_cannot_act_again_at_l2(page, seed):
    from apps.accounts.models import Role, UserRole

    UserRole.objects.create(user=seed["l1"], role=Role.objects.get(code="L2"))
    inv = _invoice(seed, 1500000, "MC-L1L2-1", to=["finance@test.local"])
    assert inv.status == "PENDING_L1"
    ref = inv.portal_ref

    login(page, "l1@test.local")
    act_on(page, ref)
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    assert _status(inv) == "PENDING_L2"

    # Not in l1's queue even though l1 now holds L2, and no 'awaiting your L2' email to l1.
    page.goto("/console/")
    expect(page.get_by_test_id("queue-table")).to_be_visible()
    expect(page.get_by_test_id(f"queue-row-{ref}")).to_have_count(0)
    assert emails_to("l1@test.local", f"[{ref}] Invoice awaiting your L2 approval") == []
    assert len(emails_to("l2@test.local", f"[{ref}] Invoice awaiting your L2 approval")) == 1

    page.goto(f"/console/invoices/{inv.pk}/")
    expect(page.get_by_test_id("cannot-act")).to_be_visible()
    expect(page.get_by_test_id("action-panel")).to_have_count(0)
    expect(page.get_by_test_id("invoice-approve-btn")).to_have_count(0)

    # Direct POST refused.
    _post(page, f"/console/invoices/{inv.pk}/", {"action": "APPROVE"})
    page.goto(f"/console/invoices/{inv.pk}/")
    expect(page.get_by_test_id("flash-error")).to_be_visible()
    assert _status(inv) == "PENDING_L2"
    assert inv.actions.filter(action="APPROVE", level="L2").count() == 0
    logout(page)

    # The real L2 can act.
    login(page, "l2@test.local")
    act_on(page, ref)
    assert _status(inv) == "PENDING_L3"


def test_finance_cannot_approve_at_l1_to_l3(page, seed):
    from apps.accounts.models import Role, UserRole

    inv = _invoice(seed, 50000, "MC-FIN-1", to=["finance@test.local"])
    assert inv.status == "PENDING_L1"

    login(page, "finance@test.local")
    page.goto("/console/")
    expect(page.get_by_test_id(f"queue-row-{inv.portal_ref}")).to_have_count(0)
    page.goto(f"/console/invoices/{inv.pk}/")
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "PENDING_L1")
    expect(page.get_by_test_id("cannot-act")).to_be_visible()
    expect(page.get_by_test_id("action-panel")).to_have_count(0)
    _post(page, f"/console/invoices/{inv.pk}/", {"action": "APPROVE"})
    assert _status(inv) == "PENDING_L1"

    # Even a Finance user who also holds L1 cannot approve an invoice they pre-checked.
    UserRole.objects.create(user=seed["finance"], role=Role.objects.get(code="L1"))
    page.goto(f"/console/invoices/{inv.pk}/")
    expect(page.get_by_test_id("cannot-act")).to_be_visible()
    expect(page.get_by_test_id("action-panel")).to_have_count(0)
    _post(page, f"/console/invoices/{inv.pk}/", {"action": "APPROVE"})
    assert _status(inv) == "PENDING_L1"

    # Same at L2 / L3 for a large invoice.
    UserRole.objects.create(user=seed["finance"], role=Role.objects.get(code="L2"))
    UserRole.objects.create(user=seed["finance"], role=Role.objects.get(code="L3"))
    big = _invoice(seed, 1500000, "MC-FIN-2", to=["finance@test.local", "l1@test.local"])
    assert big.status == "PENDING_L2"
    page.goto(f"/console/invoices/{big.pk}/")
    expect(page.get_by_test_id("cannot-act")).to_be_visible()
    big = _invoice(seed, 1500000, "MC-FIN-3", to=["finance@test.local", "l1@test.local", "l2@test.local"])
    assert big.status == "PENDING_L3"
    page.goto(f"/console/invoices/{big.pk}/")
    expect(page.get_by_test_id("cannot-act")).to_be_visible()
    expect(page.get_by_test_id("action-panel")).to_have_count(0)


def test_bank_change_holds_payment_until_finance_approves(page, seed):
    from apps.invoices.models import Payment

    inv = _invoice(seed, 50000, "MC-BANK-1", to=["finance@test.local", "l1@test.local"])
    assert inv.status == "APPROVED"

    # Vendor admin requests a bank change.
    login(page, "vendora@test.local")
    page.goto("/vendor/bank/")
    expect(page.get_by_test_id("bank-change-pending")).to_have_count(0)
    page.get_by_test_id("holder_name").fill("Acme Supplies Private Limited")
    page.get_by_test_id("account_no").fill("777766665555")
    page.get_by_test_id("ifsc").fill("SBIN0001234")
    page.get_by_test_id("bank_name").fill("State Bank of India")
    page.get_by_test_id("bank-change-submit-btn").click()
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    expect(page.get_by_test_id("bank-change-pending")).to_be_visible()
    expect(page.get_by_test_id("bank-change-submit-btn")).to_have_count(0)
    logout(page)
    assert len(emails_to("finance@test.local", "Bank details change awaiting verification")) == 1

    # Finance sees the hold; recording the payment fails.
    login(page, "finance@test.local")
    page.goto(f"/console/invoices/{inv.pk}/")
    expect(page.get_by_test_id("payment-hold")).to_be_visible()
    page.get_by_test_id("payment-record-link").click()
    expect(page.get_by_test_id("payment-hold")).to_be_visible()
    page.get_by_test_id("utr").fill("UTR-HOLD-0001")
    page.get_by_test_id("payment-submit-btn").click()
    expect(page.get_by_test_id("form-error")).to_be_visible()
    assert not Payment.objects.filter(invoice=inv).exists()
    assert _status(inv) == "APPROVED"
    assert emails_to("vendora@test.local", "Payment advice") == []

    # Finance approves the bank change.
    page.goto("/console/bank-changes/")
    card = page.get_by_test_id("bank-change-V00001")
    expect(card.get_by_test_id("new-account-no")).to_have_text("777766665555")
    card.get_by_test_id("bank-approve-btn").click()
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    expect(page.get_by_test_id("bank-changes-empty")).to_be_visible()

    # Now the payment goes through.
    page.goto(f"/console/invoices/{inv.pk}/pay/")
    expect(page.get_by_test_id("payment-hold")).to_have_count(0)
    page.get_by_test_id("utr").fill("UTR-OK-0001")
    page.get_by_test_id("payment-submit-btn").click()
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    assert Payment.objects.filter(invoice=inv, utr="UTR-OK-0001").count() == 1
    assert _status(inv) == "PAID"
    assert len(emails_to("vendora@test.local", "Payment advice")) == 1
    seed["vendor_a"].refresh_from_db()
    assert seed["vendor_a"].active_bank_account.account_last4 == "5555"
