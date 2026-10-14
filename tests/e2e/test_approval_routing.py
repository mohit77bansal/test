"""Approval routing by amount (matrix: L1 <= 1,00,000 < L2 <= 10,00,000 < L3).

At every step only the right approver sees the invoice in their queue and gets the 'awaiting your ...' email."""
import pytest
from playwright.sync_api import expect

from .conftest import act_on, emails_to, login, upload_and_submit

APPROVERS = ["finance@test.local", "finance2@test.local", "l1@test.local", "l2@test.local", "l3@test.local", "vm@test.local"]
STEP_LABEL = {"FIN_PRECHECK": "Finance pre-check", "PENDING_L1": "L1 approval", "PENDING_L2": "L2 approval", "PENDING_L3": "L3 approval"}
STEP_POOL = {
    "FIN_PRECHECK": {"finance@test.local", "finance2@test.local"},
    "PENDING_L1": {"l1@test.local"},
    "PENDING_L2": {"l2@test.local"},
    "PENDING_L3": {"l3@test.local"},
}


@pytest.fixture
def pages(browser, live_server, seed):
    """One signed-in browser context per user, so each step only needs a queue reload."""
    contexts, out = [], {}

    def get(email):
        if email not in out:
            ctx = browser.new_context(base_url=live_server.url)
            contexts.append(ctx)
            p = ctx.new_page()
            login(p, email)
            out[email] = p
        return out[email]

    yield get
    for ctx in contexts:
        ctx.close()


def _awaiting(ref, status):
    return f"[{ref}] Invoice awaiting your {STEP_LABEL[status]}"


def _assert_step(pages, ref, status, seen_emails):
    """Invoice is at `status`: exactly the pool sees it queued and has been emailed."""
    from apps.invoices.models import Invoice

    assert Invoice.objects.get(portal_ref=ref).status == status
    pool = STEP_POOL[status]
    for email in APPROVERS:
        p = pages(email)
        p.goto("/console/")
        expect(p.get_by_test_id("queue-table")).to_be_visible()
        row = p.get_by_test_id(f"queue-row-{ref}")
        if email in pool:
            expect(row).to_be_visible()
            expect(row.get_by_test_id("status-badge")).to_have_attribute("data-status", status)
        else:
            expect(row).to_have_count(0)
    # 'awaiting your <step>' email: one per pool member, nobody else.
    for email in APPROVERS:
        got = emails_to(email, _awaiting(ref, status))
        assert len(got) == (1 if email in pool else 0), (email, status, [m.subject for m in got])
    # No email for a later step went out early.
    later = [s for s in STEP_LABEL if s not in seen_emails and s != status]
    for s in later:
        for email in APPROVERS:
            assert emails_to(email, _awaiting(ref, s)) == [], (email, s)
    seen_emails.add(status)


def _assert_approved(pages, ref):
    from apps.invoices.models import Invoice

    inv = Invoice.objects.get(portal_ref=ref)
    assert inv.status == "APPROVED"
    assert inv.due_date is not None
    for email in APPROVERS:
        p = pages(email)
        p.goto("/console/")
        expect(p.get_by_test_id(f"queue-row-{ref}")).to_have_count(0)
    fin = pages("finance@test.local")
    fin.goto("/console/invoices/")
    expect(fin.get_by_test_id(f"invoice-row-{ref}").get_by_test_id("status-badge")).to_have_attribute("data-status", "APPROVED")
    assert len(emails_to("vendora@test.local", f"received — {ref}")) == 1
    assert len(emails_to("vendora@test.local", f"[{ref}] Invoice {inv.invoice_no} approved")) == 1


def _approve(pages, email, ref, **kw):
    p = pages(email)
    act_on(p, ref, **kw)
    expect(p.get_by_test_id("flash-success")).to_be_visible()


def test_50k_finance_then_l1_then_approved(pages, seed, tmp_path):
    vendor_page = pages("vendora@test.local")
    ref = upload_and_submit(vendor_page, tmp_path, total=50000, gstin=seed["vendor_a"].primary_gstin.gstin, invoice_no="ROUTE-50K")
    from apps.invoices.models import Invoice

    assert Invoice.objects.get(portal_ref=ref).required_level == "L1"
    seen = set()

    _assert_step(pages, ref, "FIN_PRECHECK", seen)
    _approve(pages, "finance@test.local", ref, tds_section="194J")

    _assert_step(pages, ref, "PENDING_L1", seen)
    _approve(pages, "l1@test.local", ref)

    _assert_approved(pages, ref)
    # L2 / L3 never involved.
    for email in ("l2@test.local", "l3@test.local"):
        assert emails_to(email, f"[{ref}]") == []
    vendor_page.goto(f"/vendor/invoices/{Invoice.objects.get(portal_ref=ref).pk}/")
    expect(vendor_page.get_by_test_id("status-badge")).to_have_attribute("data-status", "APPROVED")


def test_15_lakh_finance_l1_l2_l3_then_approved(pages, seed, tmp_path):
    vendor_page = pages("vendora@test.local")
    ref = upload_and_submit(vendor_page, tmp_path, total=1500000, gstin=seed["vendor_a"].primary_gstin.gstin, invoice_no="ROUTE-15L")
    from apps.invoices.models import Invoice

    assert Invoice.objects.get(portal_ref=ref).required_level == "L3"
    seen = set()

    _assert_step(pages, ref, "FIN_PRECHECK", seen)
    _approve(pages, "finance2@test.local", ref, tds_section="194C")

    _assert_step(pages, ref, "PENDING_L1", seen)
    _approve(pages, "l1@test.local", ref)

    _assert_step(pages, ref, "PENDING_L2", seen)
    _approve(pages, "l2@test.local", ref)

    _assert_step(pages, ref, "PENDING_L3", seen)
    _approve(pages, "l3@test.local", ref)

    _assert_approved(pages, ref)
    inv = Invoice.objects.get(portal_ref=ref)
    assert [(a.level, a.actor.email) for a in inv.actions.filter(action="APPROVE").order_by("acted_at")] == [
        ("FIN_PRE", "finance2@test.local"), ("L1", "l1@test.local"), ("L2", "l2@test.local"), ("L3", "l3@test.local"),
    ]
