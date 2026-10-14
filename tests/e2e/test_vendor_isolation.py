"""Vendor isolation (docs/ARCHITECTURE.md §1 #11-12, §3 RLS, §8 test_vendor_isolation).

Logged in as vendor A in the browser, every attempt to reach vendor B's data — by URL, by guessed
IDs, through export links, or through internal console URLs — must return 404 (never 403, never
B's data).
"""
import csv
import io
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import load_workbook
from playwright.sync_api import expect

from .conftest import login

VENDOR_A = "vendora@test.local"


# --- local helpers ---------------------------------------------------------------------------

def _submit(seed, vendor_key, by_email, *, total, invoice_no):
    from apps.accounts.models import User
    from apps.core.seed import sample_invoice
    from apps.invoices import services

    v = seed[vendor_key]
    by = User.objects.get(email=by_email)
    inv = sample_invoice(v, by, total=total, invoice_no=invoice_no)
    data = {
        "supplier_gstin": v.primary_gstin.gstin, "invoice_no": invoice_no, "invoice_date": date.today(),
        "taxable": inv.taxable, "cgst": inv.cgst, "sgst": inv.sgst, "igst": inv.igst, "total": inv.total,
        "cost_centre": seed["cost_centre"],
    }
    return services.submit(inv, by=by, data=data)


def _approve_and_pay(seed, inv, *, amount, utr):
    from apps.invoices import services

    actors = {"FIN_PRE": seed["finance"], "L1": seed["l1"], "L2": seed["l2"], "L3": seed["l3"]}
    while inv.status != "APPROVED":
        fin = {"tds_section": "194J", "tds_amount": Decimal("0")} if inv.current_level == "FIN_PRE" else None
        inv = services.act(inv, by=actors[inv.current_level], action="APPROVE", fin_data=fin)
    return services.record_payment(inv, by=seed["finance"], amount=Decimal(amount), paid_on=date.today(), utr=utr)


def _csrf_post(page, url, form=None):
    token = next(c["value"] for c in page.context.cookies() if c["name"] == "csrftoken")
    return page.request.post(url, form={**(form or {}), "csrfmiddlewaretoken": token}, headers={"X-CSRFToken": token}, max_redirects=0)


@pytest.fixture
def world(seed):
    """Vendor A and vendor B each with a paid invoice; B also has a draft, a KYC document and a user."""
    from apps.accounts.models import User
    from apps.core.seed import sample_invoice
    from apps.notify.models import Notification
    from apps.parsing.services import store_upload
    from apps.vendors.models import DocumentType, VendorDocument, VendorUser

    vb = seed["vendor_b"]
    b_admin = User.objects.get(email="vendorb@test.local")
    a_inv = _submit(seed, "vendor_a", VENDOR_A, total=50000, invoice_no="A-ISO-1")
    a_pay = _approve_and_pay(seed, a_inv, amount="20000", utr="UTRAAAA0001")
    b_inv = _submit(seed, "vendor_b", "vendorb@test.local", total=60000, invoice_no="B-SECRET-1")
    b_pay = _approve_and_pay(seed, b_inv, amount="25000", utr="UTRBBBB0001")
    b_draft = sample_invoice(vb, b_admin, total=1000, invoice_no="B-DRAFT-1")
    f = store_upload(SimpleUploadedFile("b-pan.pdf", b"%PDF-1.4 B PAN", content_type="application/pdf"), vendor=vb, user=b_admin)
    b_doc = VendorDocument.objects.create(vendor=vb, document_type=DocumentType.objects.get(code="PAN"), file=f)
    b_user = User.objects.create_user("vendorb.user@test.local", "Passw0rd!234", name="Bob B", user_type="VENDOR")
    b_vu = VendorUser.objects.create(vendor=vb, user=b_user, is_admin=False)
    b_note = Notification.objects.filter(user=b_admin).first()
    # Files stored directly (not via the upload view) were never scanned; mark them viewable so a
    # 404 below can only come from vendor isolation, not from the scan gate.
    from apps.parsing.models import File

    File.objects.update(scan_status=File.SKIPPED)
    return {
        "a_inv": a_inv, "a_pay": a_pay, "b_inv": b_inv, "b_pay": b_pay, "b_draft": b_draft, "b_doc": b_doc,
        "b_vu": b_vu, "b_note": b_note, "vendor_b": vb, "b_gstin": vb.primary_gstin, "b_bank": vb.active_bank_account,
    }


def _assert_404(page, path):
    resp = page.goto(path)
    assert resp is not None and resp.status == 404, f"GET {path} -> {resp.status if resp else None}"


def _b_markers(w):
    return [w["b_inv"].portal_ref, "B-SECRET-1", "V00002", "Bharat Traders", "UTRBBBB0001", w["vendor_b"].primary_gstin.gstin]


# --- tests -----------------------------------------------------------------------------------

def test_vendor_a_gets_404_for_vendor_b_vendor_urls(page, world):
    w = world
    login(page, VENDOR_A)
    # Sanity: A can reach its own invoice and file.
    assert page.goto(f"/vendor/invoices/{w['a_inv'].pk}/").status == 200
    assert page.request.get(f"/vendor/files/{w['a_inv'].file_id}/").status == 200

    b_inv, b_draft = w["b_inv"].pk, w["b_draft"].pk
    for path in [
        f"/vendor/invoices/{b_inv}/",
        f"/vendor/invoices/{b_inv}/edit/",
        f"/vendor/invoices/{b_inv}/parse-status/",
        f"/vendor/invoices/{b_draft}/",
        f"/vendor/invoices/{b_draft}/edit/",
        f"/vendor/invoices/{b_draft}/parse-status/",
        f"/vendor/files/{w['b_inv'].file_id}/",
        f"/vendor/files/{w['b_doc'].file_id}/",
    ]:
        _assert_404(page, path)

    # File URLs through the API request context too (no browser download/preview involved).
    for fid in (w["b_inv"].file_id, w["b_doc"].file_id):
        resp = page.request.get(f"/vendor/files/{fid}/", max_redirects=0)
        assert resp.status == 404, f"file {fid} -> {resp.status}"
        assert b"B PAN" not in resp.body() and b"B-SECRET" not in resp.body()

    # State-changing requests against B's objects are 404 as well and change nothing.
    assert _csrf_post(page, f"/vendor/invoices/{b_inv}/", {"body": "hello"}).status == 404
    assert _csrf_post(page, f"/vendor/invoices/{b_draft}/discard/").status == 404
    assert _csrf_post(page, f"/vendor/invoices/{b_draft}/edit/", {"invoice_no": "X"}).status == 404
    assert _csrf_post(page, f"/vendor/users/{w['b_vu'].pk}/remove/").status == 404
    if w["b_note"]:
        assert _csrf_post(page, f"/notifications/{w['b_note'].pk}/read/").status == 404

    from apps.invoices.models import Invoice
    from apps.vendors.models import VendorUser

    assert Invoice.objects.filter(pk=b_draft).exists()
    assert VendorUser.objects.get(pk=w["b_vu"].pk).is_active
    assert not w["b_inv"].messages.filter(body="hello").exists()


def test_vendor_b_can_reach_its_own_urls(browser, live_server, world):
    """Control for the 404s above: the same URLs work for vendor B itself."""
    w = world
    ctx = browser.new_context(base_url=live_server.url)
    page = ctx.new_page()
    try:
        login(page, "vendorb@test.local")
        assert page.goto(f"/vendor/invoices/{w['b_inv'].pk}/").status == 200
        expect(page.get_by_test_id("invoice-ref")).to_have_text(w["b_inv"].portal_ref)
        assert page.goto(f"/vendor/invoices/{w['b_draft'].pk}/parse-status/").status == 200
        for fid in (w["b_inv"].file_id, w["b_doc"].file_id):
            assert page.request.get(f"/vendor/files/{fid}/").status == 200
        page.goto("/vendor/payments/")
        expect(page.get_by_test_id("payment-utr")).to_have_text(["UTRBBBB0001"])
    finally:
        ctx.close()


def test_vendor_a_gets_404_for_vendor_b_lists_payments_and_documents(page, world):
    w = world
    login(page, VENDOR_A)
    page.goto("/vendor/invoices/")
    expect(page.get_by_test_id(f"invoice-row-{w['a_inv'].portal_ref}")).to_be_visible()
    expect(page.get_by_test_id(f"invoice-row-{w['b_inv'].portal_ref}")).to_have_count(0)
    page.goto("/vendor/payments/")
    expect(page.get_by_test_id("payment-utr")).to_have_text(["UTRAAAA0001"])
    page.goto("/vendor/onboarding/documents/")
    expect(page.get_by_test_id("doc-row-PAN")).to_be_visible()
    for p in ("/vendor/invoices/", "/vendor/payments/", "/vendor/onboarding/documents/", "/vendor/", "/vendor/bank/", "/vendor/users/", "/vendor/profile/", "/notifications/"):
        page.goto(p)
        html = page.content()
        for marker in _b_markers(w) + ["vendorb", "b-pan.pdf"]:
            assert marker not in html, f"{marker} leaked on {p}"


def test_vendor_a_gets_404_for_guessed_ids(page, world):
    login(page, VENDOR_A)
    for _ in range(2):
        rid = uuid.uuid4()
        for path in [
            f"/vendor/invoices/{rid}/", f"/vendor/invoices/{rid}/edit/", f"/vendor/invoices/{rid}/parse-status/",
            f"/vendor/files/{rid}/", f"/console/invoices/{rid}/", f"/console/invoices/{rid}/pay/",
            f"/console/files/{rid}/", f"/console/vendors/{rid}/",
        ]:
            _assert_404(page, path)
        assert page.request.get(f"/vendor/files/{rid}/", max_redirects=0).status == 404
    # Malformed / sequential-looking IDs do not resolve either.
    for path in ("/vendor/invoices/1/", "/vendor/files/1/", "/vendor/invoices/INV-2026-00001/"):
        _assert_404(page, path)


def test_vendor_a_gets_404_for_console_urls(page, world):
    w = world
    vb = w["vendor_b"]
    login(page, VENDOR_A)
    for path in [
        "/console/", "/console/vendors/", "/console/vendors/invite/", f"/console/vendors/{vb.pk}/",
        f"/console/vendors/{w['a_inv'].vendor_id}/",
        "/console/invoices/", "/console/invoices/?export=csv", f"/console/invoices/{w['b_inv'].pk}/",
        f"/console/invoices/{w['a_inv'].pk}/", f"/console/invoices/{w['b_inv'].pk}/pay/",
        "/console/payments/queue/", f"/console/files/{w['b_inv'].file_id}/", f"/console/files/{w['b_doc'].file_id}/",
        "/console/bank-changes/", "/console/reports/ageing/", "/console/reports/vendor-master/?export=csv",
        "/console/reports/payments/?export=xlsx", "/console/admin/users/", "/console/admin/matrix/",
        "/console/admin/documents/", "/console/admin/templates/", "/console/audit/", "/console/audit/?export=csv",
    ]:
        _assert_404(page, path)
    assert page.request.get(f"/console/files/{w['b_inv'].file_id}/", max_redirects=0).status == 404

    # Console POST endpoints, with a valid CSRF token, are 404 too and do nothing.
    posts = [
        (f"/console/invoices/{w['b_inv'].pk}/", {"action": "REJECT", "comment": "x"}),
        (f"/console/invoices/{w['b_inv'].pk}/pay/", {"amount": "1", "paid_on": date.today().isoformat(), "utr": "HACK"}),
        (f"/console/vendors/{vb.pk}/block/", {"reason": "x"}),
        (f"/console/vendors/{vb.pk}/documents/{w['b_doc'].pk}/review/", {"decision": "approve"}),
        (f"/console/vendors/{vb.pk}/gstins/{w['b_gstin'].pk}/verify/", {}),
        (f"/console/bank-changes/{w['b_bank'].pk}/decide/", {"decision": "reject", "remarks": "x"}),
        ("/console/admin/templates/invoice_approved/toggle/", {}),
    ]
    for url, form in posts:
        resp = _csrf_post(page, url, form)
        assert resp.status == 404, f"POST {url} -> {resp.status}"

    from apps.invoices.models import Invoice
    from apps.notify.models import EmailTemplate
    from apps.vendors.models import Vendor

    b_inv = Invoice.objects.get(pk=w["b_inv"].pk)
    assert b_inv.status == "PARTLY_PAID" and b_inv.payments.count() == 1
    assert Vendor.objects.get(pk=vb.pk).status == "ACTIVE"
    assert EmailTemplate.objects.get(code="invoice_approved").enabled


@pytest.mark.parametrize("path", ["/vendor/invoices/", "/vendor/invoices/?status=PARTLY_PAID", "/vendor/payments/"])
def test_vendor_a_exports_contain_no_vendor_b_data(page, world, path):
    w = world
    login(page, VENDOR_A)
    page.goto(path)

    # Through the export buttons on the page.
    with page.expect_download() as dl:
        page.get_by_test_id("export-csv-btn").click()
    rows = list(csv.reader(io.StringIO(open(dl.value.path(), encoding="utf-8").read())))
    with page.expect_download() as dl:
        page.get_by_test_id("export-xlsx-btn").click()
    ws = load_workbook(io.BytesIO(open(dl.value.path(), "rb").read())).active
    xrows = [[("" if c is None else str(c)) for c in r] for r in ws.iter_rows(values_only=True)]

    for table in (rows, xrows):
        flat = "\n".join(",".join(r) for r in table)
        assert len(table) == 2, f"expected header + vendor A's one row, got {table}"
        assert "V00001" in flat or "UTRAAAA0001" in flat
        for marker in _b_markers(w):
            assert marker not in flat, f"{marker} leaked in export of {path}"

    # And by typing the export URL, including attempts to widen it.
    sep = "&" if "?" in path else "?"
    for extra in ("export=csv", "export=csv&vendor=V00002", f"export=csv&vendor_id={w['vendor_b'].pk}", "export=csv&status="):
        resp = page.request.get(f"{path}{sep}{extra}")
        assert resp.status == 200
        body = resp.text()
        for marker in _b_markers(w):
            assert marker not in body, f"{marker} leaked in {path}{sep}{extra}"
