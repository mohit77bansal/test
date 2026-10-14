"""Payments (docs/ARCHITECTURE.md §5 last row, §1 #8, §8 test_payments).

Finance records full and partial payments (UTR + TDS) at /console/invoices/<id>/pay/; status goes
PARTLY_PAID then PAID; the vendor sees status, payment history and outstanding; a payment advice
email carries the amount and UTR; overpayment (amount + TDS > outstanding) is rejected.
"""
from datetime import date
from decimal import Decimal

import pytest
from django.core import mail
from playwright.sync_api import expect

from .conftest import emails_to, login

VENDOR_UPLOADER = "vendora.user@test.local"
VENDOR_ADMIN = "vendora@test.local"


# --- local helpers ---------------------------------------------------------------------------

def _approved_invoice(seed, *, total, invoice_no, tds=Decimal("1000"), vendor="vendor_a", by_email=VENDOR_UPLOADER):
    """Submit (as the vendor user) and approve through every required level via the services."""
    from apps.accounts.models import User
    from apps.core.seed import sample_invoice
    from apps.invoices import services

    v = seed[vendor]
    by = User.objects.get(email=by_email)
    inv = sample_invoice(v, by, total=total, invoice_no=invoice_no)
    data = {
        "supplier_gstin": v.primary_gstin.gstin, "invoice_no": invoice_no, "invoice_date": date.today(),
        "taxable": inv.taxable, "cgst": inv.cgst, "sgst": inv.sgst, "igst": inv.igst, "total": inv.total,
        "cost_centre": seed["cost_centre"],
    }
    inv = services.submit(inv, by=by, data=data)
    actors = {"FIN_PRE": seed["finance"], "L1": seed["l1"], "L2": seed["l2"], "L3": seed["l3"]}
    while inv.status != "APPROVED":
        fin = {"tds_section": "194J", "tds_amount": tds} if inv.current_level == "FIN_PRE" else None
        inv = services.act(inv, by=actors[inv.current_level], action="APPROVE", fin_data=fin)
    return inv


def _reload(inv):
    from apps.invoices.models import Invoice

    return Invoice.objects.get(pk=inv.pk)


def _text(m):
    return m.body + "".join(content for content, _ in getattr(m, "alternatives", []))


def _pay(page, inv, *, amount, utr, tds="0", paid_on=None):
    page.goto(f"/console/invoices/{inv.pk}/pay/")
    page.get_by_test_id("amount").fill(str(amount))
    page.get_by_test_id("tds_deducted").fill(str(tds))
    if paid_on:
        page.get_by_test_id("paid_on").fill(paid_on)
    page.get_by_test_id("utr").fill(utr)
    page.get_by_test_id("payment-submit-btn").click()


@pytest.fixture
def vendor_page(browser, live_server):
    ctx = browser.new_context(base_url=live_server.url)
    yield ctx.new_page()
    ctx.close()


# --- tests -----------------------------------------------------------------------------------

def test_partial_then_full_payment_with_utr_and_tds(page, vendor_page, seed):
    inv = _approved_invoice(seed, total=100000, invoice_no="PAY-1", tds=Decimal("1000"))
    ref = inv.portal_ref

    login(page, "finance@test.local")
    page.goto("/console/payments/queue/")
    page.get_by_test_id("payment-record-link").first.wait_for()
    page.goto(f"/console/invoices/{inv.pk}/pay/")
    expect(page.get_by_test_id("outstanding")).to_have_text("₹1,00,000.00")

    # 1) Partial payment: ₹40,000 paid + ₹1,000 TDS -> outstanding ₹59,000.
    sent = len(mail.outbox)
    _pay(page, inv, amount="40000", tds="1000", utr="UTR000000000001", paid_on=date.today().isoformat())
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    inv = _reload(inv)
    assert inv.status == "PARTLY_PAID"
    assert inv.outstanding == Decimal("59000")
    p = inv.payments.get()
    assert (p.amount, p.tds_deducted, p.utr, p.paid_on) == (Decimal("40000"), Decimal("1000"), "UTR000000000001", date.today())

    advice = [m for m in mail.outbox[sent:] if f"[{ref}] Payment advice for invoice PAY-1" in m.subject]
    assert len(advice) == 1
    assert VENDOR_ADMIN in advice[0].to and VENDOR_UPLOADER in advice[0].cc
    body = _text(advice[0])
    assert "40000" in body and "UTR000000000001" in body and "59000" in body

    # Vendor sees PARTLY_PAID, the payment and what is still outstanding.
    login(vendor_page, VENDOR_UPLOADER)
    vendor_page.goto(f"/vendor/invoices/{inv.pk}/")
    expect(vendor_page.get_by_test_id("status-badge")).to_have_attribute("data-status", "PARTLY_PAID")
    expect(vendor_page.get_by_test_id("payment-row")).to_have_count(1)
    expect(vendor_page.get_by_test_id("invoice-paid")).to_have_text("₹40,000.00")
    expect(vendor_page.get_by_test_id("invoice-outstanding")).to_have_text("₹59,000.00")
    vendor_page.goto("/vendor/payments/")
    expect(vendor_page.get_by_test_id("payment-row")).to_have_count(1)
    expect(vendor_page.get_by_test_id("payment-utr")).to_have_text("UTR000000000001")
    expect(vendor_page.get_by_test_id(f"outstanding-row-{ref}")).to_contain_text("₹59,000.00")

    # 2) Final payment of the balance -> PAID.
    page.goto(f"/console/invoices/{inv.pk}/pay/")
    expect(page.get_by_test_id("outstanding")).to_have_text("₹59,000.00")
    sent = len(mail.outbox)
    _pay(page, inv, amount="59000", tds="0", utr="UTR000000000002")
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    inv = _reload(inv)
    assert inv.status == "PAID"
    assert inv.outstanding == Decimal("0")
    advice = [m for m in mail.outbox[sent:] if f"[{ref}] Payment advice" in m.subject]
    assert len(advice) == 1
    assert "59000" in _text(advice[0]) and "UTR000000000002" in _text(advice[0])

    # Console detail shows both payments and no "record payment" link any more.
    page.goto(f"/console/invoices/{inv.pk}/")
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "PAID")
    expect(page.get_by_test_id("payment-row")).to_have_count(2)
    # A paid invoice cannot be paid again.
    assert page.goto(f"/console/invoices/{inv.pk}/pay/").status == 404

    vendor_page.goto("/vendor/invoices/")
    expect(vendor_page.get_by_test_id(f"invoice-row-{ref}").get_by_test_id("status-badge")).to_have_attribute("data-status", "PAID")
    vendor_page.goto("/vendor/payments/")
    expect(vendor_page.get_by_test_id("payment-row")).to_have_count(2)
    utrs = sorted(vendor_page.get_by_test_id("payment-utr").all_inner_texts())
    assert utrs == ["UTR000000000001", "UTR000000000002"]
    expect(vendor_page.get_by_test_id(f"outstanding-row-{ref}")).to_have_count(0)

    # The console no longer offers "Record payment" on a PAID invoice.
    page.goto(f"/console/invoices/{inv.pk}/")
    expect(page.get_by_test_id("payment-record-link")).to_have_count(0)


def test_full_payment_in_one_go(page, vendor_page, seed):
    inv = _approved_invoice(seed, total=50000, invoice_no="PAY-FULL", tds=Decimal("500"))
    login(page, "finance@test.local")
    _pay(page, inv, amount="49500", tds="500", utr="UTRFULL0001")
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    assert _reload(inv).status == "PAID"
    advice = emails_to(VENDOR_ADMIN, f"[{inv.portal_ref}] Payment advice")
    assert len(advice) == 1 and "49500" in _text(advice[0]) and "UTRFULL0001" in _text(advice[0])

    login(vendor_page, VENDOR_ADMIN)
    vendor_page.goto(f"/vendor/invoices/{inv.pk}/")
    expect(vendor_page.get_by_test_id("status-badge")).to_have_attribute("data-status", "PAID")
    expect(vendor_page.get_by_test_id("invoice-outstanding")).to_have_text("₹0.00")
    vendor_page.goto("/vendor/payments/")
    expect(vendor_page.get_by_test_id("payment-utr")).to_have_text("UTRFULL0001")


def test_overpayment_is_rejected(page, seed):
    inv = _approved_invoice(seed, total=50000, invoice_no="PAY-OVER", tds=Decimal("0"))
    login(page, "finance@test.local")
    sent = len(mail.outbox)

    # Amount alone above the total.
    _pay(page, inv, amount="50000.01", utr="UTROVER1")
    expect(page.get_by_test_id("error-amount")).to_be_visible()
    # Amount + TDS above the total.
    _pay(page, inv, amount="49500", tds="501", utr="UTROVER2")
    expect(page.get_by_test_id("error-amount")).to_be_visible()
    assert _reload(inv).status == "APPROVED"
    assert not _reload(inv).payments.exists()

    # Partial, then try to overpay the remainder.
    _pay(page, inv, amount="30000", utr="UTROVER3")
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    _pay(page, inv, amount="20000.01", utr="UTROVER4")
    expect(page.get_by_test_id("error-amount")).to_be_visible()
    expect(page.get_by_test_id("outstanding")).to_have_text("₹20,000.00")
    inv = _reload(inv)
    assert inv.status == "PARTLY_PAID"
    assert list(inv.payments.values_list("utr", flat=True)) == ["UTROVER3"]
    advice = [m for m in mail.outbox[sent:] if "Payment advice" in m.subject]
    assert len(advice) == 1 and "UTROVER3" in _text(advice[0])


def test_utr_is_required(page, seed):
    inv = _approved_invoice(seed, total=50000, invoice_no="PAY-NOUTR", tds=Decimal("0"))
    login(page, "finance@test.local")
    page.goto(f"/console/invoices/{inv.pk}/pay/")
    page.get_by_test_id("amount").fill("1000")
    page.get_by_test_id("utr").fill("")
    page.get_by_test_id("payment-submit-btn").click()
    expect(page.get_by_test_id("error-utr")).to_be_visible()
    assert not _reload(inv).payments.exists()
