"""Query and reject (docs/ARCHITECTURE.md §5 invoice state machine, §8 test_query_reject).

- A query raised at L2 goes to the vendor (email + query panel); the vendor's reply returns the
  invoice to PENDING_L2, emails the L2 approver who raised it and puts it back in their queue.
- Reject at any level is final: status REJECTED, no action panel, vendor emailed with the reason.
- A comment is mandatory to reject or query.
"""
from datetime import date
from decimal import Decimal

import pytest
from django.core import mail
from playwright.sync_api import expect

from .conftest import act_on, emails_to, login, logout

VENDOR_UPLOADER = "vendora.user@test.local"
VENDOR_ADMIN = "vendora@test.local"


# --- local helpers (state set-up through the services; behaviour under test goes through the browser)

def _submit(seed, *, total, invoice_no, by_email=VENDOR_UPLOADER):
    from apps.accounts.models import User
    from apps.core.seed import sample_invoice
    from apps.invoices import services

    vendor = seed["vendor_a"]
    by = User.objects.get(email=by_email)
    inv = sample_invoice(vendor, by, total=total, invoice_no=invoice_no)
    data = {
        "supplier_gstin": vendor.primary_gstin.gstin, "invoice_no": invoice_no, "invoice_date": date.today(),
        "taxable": inv.taxable, "cgst": inv.cgst, "sgst": inv.sgst, "igst": inv.igst, "total": inv.total,
        "cost_centre": seed["cost_centre"],
    }
    return services.submit(inv, by=by, data=data)


def _advance_to(seed, inv, level):
    """Approve through the levels (as finance, l1, l2) until the invoice waits at `level`."""
    from apps.invoices import services
    from apps.invoices.models import Invoice

    actors = {"FIN_PRE": seed["finance"], "L1": seed["l1"], "L2": seed["l2"], "L3": seed["l3"]}
    inv = Invoice.objects.get(pk=inv.pk)
    while inv.current_level != level:
        cur = inv.current_level
        fin = {"tds_section": "194J", "tds_amount": Decimal("1000")} if cur == "FIN_PRE" else None
        inv = services.act(inv, by=actors[cur], action="APPROVE", fin_data=fin)
    return inv


def _reload(inv):
    from apps.invoices.models import Invoice

    return Invoice.objects.get(pk=inv.pk)


def _text(m):
    return m.body + "".join(content for content, _ in getattr(m, "alternatives", []))


def _csrf_post(page, url, form):
    token = next(c["value"] for c in page.context.cookies() if c["name"] == "csrftoken")
    return page.request.post(url, form={**form, "csrfmiddlewaretoken": token}, headers={"X-CSRFToken": token}, max_redirects=0)


# --- tests ----------------------------------------------------------------------------------

def test_query_at_l2_vendor_reply_returns_to_l2(page, seed):
    inv = _advance_to(seed, _submit(seed, total=500000, invoice_no="QRY-L2-1"), "L2")
    ref = inv.portal_ref
    question = "Please attach the signed delivery challan for line 2."

    login(page, "l2@test.local")
    act_on(page, ref, "query", comment=question)
    expect(page.get_by_test_id(f"queue-row-{ref}")).to_have_count(0)
    inv = _reload(inv)
    assert inv.status == "QUERY_RAISED"
    assert inv.query_level == "L2"
    logout(page)

    # The uploader and the vendor admin get the query email, the L2 approver is cc'd.
    for addr in (VENDOR_UPLOADER, VENDOR_ADMIN):
        msgs = emails_to(addr, f"[{ref}] Query on invoice QRY-L2-1")
        assert len(msgs) == 1, f"query email to {addr}"
        assert question in _text(msgs[0])
    assert "l2@test.local" in emails_to(VENDOR_UPLOADER, f"[{ref}] Query on invoice")[0].cc

    # Vendor sees the query panel with the question and replies.
    login(page, VENDOR_UPLOADER)
    page.goto(f"/vendor/invoices/{inv.pk}/")
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "QUERY_RAISED")
    panel = page.get_by_test_id("query-panel")
    expect(panel).to_be_visible()
    expect(panel.get_by_test_id("message-body").first).to_contain_text(question)
    reply = "Challan attached; line 2 was delivered on 3 Oct."
    page.get_by_test_id("reply-body").fill(reply)
    page.get_by_test_id("reply-send-btn").click()
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "PENDING_L2")
    expect(page.get_by_test_id("query-panel")).to_have_count(0)
    expect(page.get_by_test_id("message-body").filter(has_text=reply)).to_have_count(1)
    logout(page)

    inv = _reload(inv)
    assert (inv.status, inv.current_level, inv.query_level) == ("PENDING_L2", "L2", "")

    # The L2 approver who raised the query is told and sees it back in the queue.
    replied = emails_to("l2@test.local", f"[{ref}] Vendor replied to your query")
    assert len(replied) == 1
    assert reply in _text(replied[0])
    assert not emails_to("l1@test.local", f"[{ref}] Vendor replied")

    # L2 resumes where it left off (from the invoice page) and can approve (₹5L needs only L2).
    login(page, "l2@test.local")
    page.goto(f"/console/invoices/{inv.pk}/")
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "PENDING_L2")
    expect(page.get_by_test_id("action-panel")).to_be_visible()
    page.get_by_test_id("invoice-approve-btn").click()
    page.get_by_test_id("queue-table").wait_for()
    assert _reload(inv).status == "APPROVED"
    logout(page)

    # It never went back to L1.
    login(page, "l1@test.local")
    page.goto("/console/")
    expect(page.get_by_test_id(f"queue-row-{ref}")).to_have_count(0)


def test_replied_query_is_back_in_l2_queue(page, seed):
    """After the vendor replies, the invoice is back in the queue of the L2 approver who raised the query."""
    from apps.accounts.models import User
    from apps.invoices import services

    inv = _advance_to(seed, _submit(seed, total=500000, invoice_no="QRY-L2-Q"), "L2")
    ref = inv.portal_ref
    login(page, "l2@test.local")
    act_on(page, ref, "query", comment="Which PO does this relate to?")
    expect(page.get_by_test_id(f"queue-row-{ref}")).to_have_count(0)
    services.vendor_reply(_reload(inv), by=User.objects.get(email=VENDOR_UPLOADER), body="PO 4500012345.")
    assert _reload(inv).status == "PENDING_L2"

    page.goto("/console/")
    row = page.get_by_test_id(f"queue-row-{ref}")
    expect(row).to_be_visible()
    expect(row.get_by_test_id("status-badge")).to_have_attribute("data-status", "PENDING_L2")
    act_on(page, ref, "approve")
    assert _reload(inv).status == "APPROVED"


@pytest.mark.parametrize("level, actor", [("FIN_PRE", "finance"), ("L1", "l1"), ("L2", "l2"), ("L3", "l3")])
def test_reject_at_any_level_is_final(page, seed, level, actor):
    inv = _advance_to(seed, _submit(seed, total=1500000, invoice_no=f"REJ-{level}"), level)
    ref = inv.portal_ref
    reason = f"Rate does not match the contract ({level})."

    login(page, f"{actor}@test.local")
    act_on(page, ref, "reject", comment=reason)
    expect(page.get_by_test_id(f"queue-row-{ref}")).to_have_count(0)
    assert _reload(inv).status == "REJECTED"

    # Detail: REJECTED badge, no action panel.
    page.goto(f"/console/invoices/{inv.pk}/")
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "REJECTED")
    expect(page.get_by_test_id("action-panel")).to_have_count(0)
    expect(page.get_by_test_id("invoice-approve-btn")).to_have_count(0)
    rejects = page.get_by_test_id("history-row").and_(page.locator("[data-action='REJECT']"))
    expect(rejects).to_have_count(1)
    expect(rejects).to_contain_text(reason)

    # A crafted POST cannot revive it.
    resp = _csrf_post(page, f"/console/invoices/{inv.pk}/", {"action": "APPROVE", "comment": "", "tds_section": "194J"})
    assert resp.status in (302, 303)
    assert _reload(inv).status == "REJECTED"
    logout(page)

    # Nobody else can act on it either.
    login(page, "l3@test.local" if actor != "l3" else "l2@test.local")
    page.goto(f"/console/invoices/{inv.pk}/")
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "REJECTED")
    expect(page.get_by_test_id("action-panel")).to_have_count(0)
    page.goto("/console/")
    expect(page.get_by_test_id(f"queue-row-{ref}")).to_have_count(0)
    logout(page)

    # Vendor is emailed with the reason and sees the final status (no reply panel).
    for addr in (VENDOR_UPLOADER, VENDOR_ADMIN):
        msgs = emails_to(addr, f"[{ref}] Invoice REJ-{level} rejected")
        assert len(msgs) == 1, f"reject email to {addr}"
        assert reason in _text(msgs[0])
    login(page, VENDOR_UPLOADER)
    page.goto(f"/vendor/invoices/{inv.pk}/")
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "REJECTED")
    expect(page.get_by_test_id("query-panel")).to_have_count(0)
    expect(page.get_by_test_id("reply-send-btn")).to_have_count(0)


@pytest.mark.parametrize("action", ["reject", "query"])
@pytest.mark.parametrize("level, actor", [("FIN_PRE", "finance"), ("L1", "l1")])
def test_comment_mandatory_for_reject_and_query(page, seed, action, level, actor):
    inv = _advance_to(seed, _submit(seed, total=50000, invoice_no=f"CMT-{action}-{level}"), level)
    ref = inv.portal_ref
    before_status = inv.status
    sent_before = len(mail.outbox)

    login(page, f"{actor}@test.local")
    page.goto("/console/")
    page.get_by_test_id(f"queue-open-{ref}").click()
    page.get_by_test_id(f"invoice-{action}-btn").click()
    expect(page.get_by_test_id("action-error")).to_be_visible()
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", before_status)

    # Whitespace only is not a comment either.
    page.get_by_test_id("action-comment").fill("   ")
    page.get_by_test_id(f"invoice-{action}-btn").click()
    expect(page.get_by_test_id("action-error")).to_be_visible()
    expect(page.get_by_test_id("action-panel")).to_be_visible()

    inv = _reload(inv)
    assert inv.status == before_status
    assert not inv.actions.filter(action__in=["REJECT", "QUERY"]).exists()
    assert not [m for m in mail.outbox[sent_before:] if ref in m.subject and ("rejected" in m.subject or "Query" in m.subject)]

    # With a comment the same action goes through.
    page.get_by_test_id("action-comment").fill("Missing PO reference.")
    page.get_by_test_id(f"invoice-{action}-btn").click()
    expect(page.get_by_test_id(f"queue-row-{ref}")).to_have_count(0)
    assert _reload(inv).status == {"reject": "REJECTED", "query": "QUERY_RAISED"}[action]
