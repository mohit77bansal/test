"""Audit log (docs/ARCHITECTURE.md §3 audit_log, §5 "every transition: ApprovalAction + AuditLog",
§8 test_audit).

Every approval, status change, bank change and login appears in /console/audit/ (admin or auditor),
filterable by entity_id / action / actor, with the actor and a timestamp.
"""
import re
from datetime import date, timedelta

from django.utils import timezone
from playwright.sync_api import expect

from .conftest import act_on, login, logout

TIMESTAMP = re.compile(r"\d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2}")


# --- local helpers ---------------------------------------------------------------------------

def _submit(seed, *, total, invoice_no, by_email="vendora.user@test.local"):
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


def _rows(page, action=None):
    rows = page.get_by_test_id("audit-row")
    return rows.and_(page.locator(f'[data-action="{action}"]')) if action else rows


def _audit(page, **params):
    from urllib.parse import urlencode

    page.goto("/console/audit/?" + urlencode(params))
    page.get_by_test_id("audit-table").wait_for()


def _assert_entry(row, actor_email):
    if actor_email:
        expect(row.get_by_test_id("audit-actor")).to_have_text(actor_email)
    else:
        expect(row.get_by_test_id("audit-actor")).not_to_have_text("system")
    expect(row).to_contain_text(TIMESTAMP)


# --- tests -----------------------------------------------------------------------------------

def test_approvals_and_status_changes_are_audited(page, seed):
    from apps.audit.models import AuditLog

    started = timezone.now() - timedelta(seconds=5)
    inv = _submit(seed, total=500000, invoice_no="AUD-1")   # needs L2
    ref = inv.portal_ref

    login(page, "finance@test.local")
    act_on(page, ref, "approve", tds_section="194J")
    logout(page)
    login(page, "l1@test.local")
    act_on(page, ref, "approve", comment="OK at L1")
    logout(page)
    login(page, "l2@test.local")
    act_on(page, ref, "query", comment="Need PO copy")
    logout(page)

    login(page, "auditor@test.local")
    # Filter through the form: entity id.
    page.goto("/console/audit/")
    page.get_by_test_id("audit-filter-entity-id").fill(str(inv.pk))
    page.get_by_test_id("audit-filter-entity-id").press("Enter")
    page.wait_for_url(re.compile(r"entity_id="))
    rows = _rows(page)
    expect(rows.first).to_be_visible()
    # Every row is about this invoice.
    assert all(str(inv.pk) in t for t in rows.all_inner_texts())

    approvals = _rows(page, "invoice.approve")
    expect(approvals).to_have_count(2)
    _assert_entry(approvals.filter(has_text="FIN_PRE"), "finance@test.local")
    _assert_entry(approvals.filter(has_text="OK at L1"), "l1@test.local")
    _assert_entry(_rows(page, "invoice.query"), "l2@test.local")
    _assert_entry(_rows(page, "invoice.submit"), "vendora.user@test.local")

    # Status changes: SUBMITTED -> FIN_PRECHECK -> PENDING_L1 -> PENDING_L2 (+ the QUERY_RAISED on the query row).
    statuses = _rows(page, "invoice.status")
    expect(statuses).to_have_count(3)
    for target in ("FIN_PRECHECK", "PENDING_L1", "PENDING_L2"):
        expect(statuses.filter(has_text=target).first).to_be_visible()
        _assert_entry(statuses.filter(has_text=target).first, None)
    transitions = sorted(
        (e.before_json["status"], e.after_json["status"])
        for e in AuditLog.objects.filter(entity="invoice", entity_id=str(inv.pk), action="invoice.status")
    )
    assert transitions == [("FIN_PRECHECK", "PENDING_L1"), ("PENDING_L1", "PENDING_L2"), ("SUBMITTED", "FIN_PRECHECK")]
    expect(_rows(page, "invoice.query")).to_contain_text("QUERY_RAISED")

    # Filter by action through the form as well.
    page.get_by_test_id("audit-filter-action").fill("invoice.approve")
    page.get_by_test_id("audit-filter-action").press("Enter")
    page.wait_for_url(re.compile(r"action=invoice.approve"))
    expect(_rows(page)).to_have_count(2)

    # Stored with actor and timestamp.
    entries = AuditLog.objects.filter(entity="invoice", entity_id=str(inv.pk))
    assert entries.filter(action="invoice.approve").count() == 2
    assert all(e.actor_id and e.at >= started for e in entries)


def test_reject_and_vendor_status_change_are_audited(page, seed):
    vendor_b = seed["vendor_b"]
    inv = _submit(seed, total=50000, invoice_no="AUD-REJ")

    login(page, "finance@test.local")
    act_on(page, inv.portal_ref, "reject", comment="Wrong GST rate")
    logout(page)

    login(page, "vm@test.local")
    page.goto(f"/console/vendors/{vendor_b.pk}/")
    page.get_by_test_id("block-reason").fill("Compliance hold")
    page.get_by_test_id("vendor-block-btn").click()
    expect(page.get_by_test_id("vendor-unblock-btn")).to_be_visible()
    page.get_by_test_id("vendor-unblock-btn").click()
    expect(page.get_by_test_id("vendor-block-btn")).to_be_visible()
    logout(page)

    login(page, "admin@test.local")
    _audit(page, entity_id=str(inv.pk), action="invoice.reject")
    row = _rows(page, "invoice.reject")
    expect(row).to_have_count(1)
    _assert_entry(row, "finance@test.local")
    expect(row).to_contain_text("REJECTED")
    expect(row).to_contain_text("Wrong GST rate")

    _audit(page, entity_id=str(vendor_b.pk))
    blocked, unblocked = _rows(page, "vendor.blocked"), _rows(page, "vendor.unblocked")
    expect(blocked).to_have_count(1)
    expect(unblocked).to_have_count(1)
    _assert_entry(blocked, "vm@test.local")
    _assert_entry(unblocked, "vm@test.local")
    expect(blocked).to_contain_text("BLOCKED")
    expect(blocked).to_contain_text("Compliance hold")


def test_bank_change_request_and_approval_are_audited(page, seed):
    from apps.vendors.models import VendorBankAccount

    login(page, "vendora@test.local")
    page.goto("/vendor/bank/")
    page.get_by_test_id("holder_name").fill("Acme Supplies Private Limited")
    page.get_by_test_id("account_no").fill("998877665544")
    page.get_by_test_id("ifsc").fill("ICIC0001234")
    page.get_by_test_id("bank_name").fill("ICICI Bank")
    page.get_by_test_id("bank-change-submit-btn").click()
    expect(page.get_by_test_id("bank-change-pending")).to_be_visible()
    logout(page)
    acct = VendorBankAccount.objects.get(vendor=seed["vendor_a"], status="PENDING")

    login(page, "finance@test.local")
    page.goto("/console/bank-changes/")
    page.get_by_test_id("bank-change-V00001").get_by_test_id("bank-approve-btn").click()
    expect(page.get_by_test_id("bank-changes-empty")).to_be_visible()
    logout(page)

    login(page, "auditor@test.local")
    _audit(page, entity_id=str(acct.pk))
    requested, approved = _rows(page, "bank.change_requested"), _rows(page, "bank.change_approved")
    expect(requested).to_have_count(1)
    expect(approved).to_have_count(1)
    _assert_entry(requested, "vendora@test.local")
    _assert_entry(approved, "finance@test.local")
    expect(requested).to_contain_text("5544")          # last 4 only
    expect(requested).not_to_contain_text("998877665544")
    expect(approved).to_contain_text("ACTIVE")


def test_logins_are_audited(page, seed):
    from apps.accounts.models import User

    for email in ("vendora@test.local", "l1@test.local"):
        login(page, email)
        logout(page)
    login(page, "admin@test.local")

    for email in ("vendora@test.local", "l1@test.local", "admin@test.local"):
        user = User.objects.get(email=email)
        _audit(page, action="auth.login", entity_id=str(user.pk))
        rows = _rows(page, "auth.login")
        expect(rows).to_have_count(1)
        _assert_entry(rows, email)

    # Filter by actor through the form.
    page.goto("/console/audit/")
    page.get_by_test_id("audit-filter-actor").fill("vendora@test.local")
    page.get_by_test_id("audit-filter-action").fill("auth.")
    page.get_by_test_id("audit-filter-action").press("Enter")
    page.wait_for_url(re.compile(r"actor="))
    expect(_rows(page, "auth.login")).to_have_count(1)
    expect(_rows(page, "auth.logout")).to_have_count(1)
    expect(_rows(page).get_by_test_id("audit-actor")).to_have_text(["vendora@test.local"] * _rows(page).count())


def test_audit_log_visible_to_admin_and_auditor_only(page, seed):
    login(page, "l1@test.local")
    assert page.goto("/console/audit/").status == 403
    logout(page)
    login(page, "auditor@test.local")
    assert page.goto("/console/audit/").status == 200
    expect(page.get_by_test_id("audit-table")).to_be_visible()
