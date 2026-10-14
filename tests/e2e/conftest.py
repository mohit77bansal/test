"""Playwright e2e fixtures. Tests run against pytest-django's live_server with
PARSER_ENGINE=fake, the locmem email backend (assert on mail.outbox) and eager Celery.

Each test starts from the seed state (transactional DB flush + seed_demo)."""
import os
import re
from decimal import Decimal

import pytest
from django.core import mail

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

PW = "Passw0rd!234"


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or ("/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)
    return {**browser_type_launch_args, **({"executable_path": exe} if exe else {})}


@pytest.fixture
def seed(transactional_db, live_server):
    from apps.core.seed import seed_demo

    return seed_demo()


@pytest.fixture(scope="session")
def base_url(live_server):
    return live_server.url


def last_otp(email):
    for m in reversed(mail.outbox):
        if email in m.to and "sign-in code" in m.subject:
            return re.search(r"\b(\d{6})\b", m.subject).group(1)
    raise AssertionError(f"No OTP email for {email}")


def emails_to(email, subject_contains=""):
    return [m for m in mail.outbox if (email in m.to or email in m.cc) and subject_contains in m.subject]


def login(page, email, password=PW):
    page.goto("/login/")
    page.get_by_test_id("login-email").fill(email)
    page.get_by_test_id("login-password").fill(password)
    page.get_by_test_id("login-submit-btn").click()
    page.get_by_test_id("otp-code").fill(last_otp(email))
    page.get_by_test_id("otp-submit-btn").click()
    page.get_by_test_id("current-user").wait_for()


def logout(page):
    page.get_by_test_id("logout-btn").click()
    page.get_by_test_id("login-email").wait_for()


def fake_invoice_pdf(path, **fields):
    """Write a tiny PDF whose fake-parser output is controlled by `fields`."""
    import json

    marker = f"FAKE_PARSE:{json.dumps(fields)}:END".encode() if fields else b""
    path.write_bytes(b"%PDF-1.4\n" + marker + b"\n%%EOF")
    return str(path)


def invoice_fields(total, gstin, invoice_no):
    total = Decimal(str(total))
    taxable = (total / Decimal("1.18")).quantize(Decimal("0.01"))
    gst = total - taxable
    half = (gst / 2).quantize(Decimal("0.01"))
    return {"supplier_gstin": gstin, "invoice_no": invoice_no, "taxable": str(taxable), "cgst": str(half), "sgst": str(gst - half), "igst": "0", "total": str(total)}


def upload_and_submit(page, tmp_path, *, total, gstin, invoice_no, cost_centre="CC-OPS"):
    """As a logged-in vendor user: upload an invoice, check the parsed form, choose the cost centre and submit."""
    page.goto("/vendor/invoices/upload/")
    page.get_by_test_id("invoice-file").set_input_files(fake_invoice_pdf(tmp_path / f"{invoice_no}.pdf", **invoice_fields(total, gstin, invoice_no)))
    page.get_by_test_id("invoice-upload-btn").click()
    page.get_by_test_id("invoice-submit-btn").wait_for()
    select_containing(page.get_by_test_id("cost_centre"), cost_centre)
    page.get_by_test_id("invoice-submit-btn").click()
    page.get_by_test_id("invoice-ref").wait_for()
    return page.get_by_test_id("invoice-ref").inner_text()


def select_containing(select, text):
    """Select the first <option> whose label contains `text`."""
    value = select.evaluate("(el, t) => [...el.options].find(o => o.text.includes(t)).value", text)
    select.select_option(value)


def act_on(page, portal_ref, action="approve", comment="", tds_section=None):
    """As a logged-in internal user: open the invoice from the queue and act on it."""
    page.goto("/console/")
    page.get_by_test_id(f"queue-open-{portal_ref}").click()
    if tds_section:
        page.get_by_test_id("tds_section").select_option(tds_section)
    if comment:
        page.get_by_test_id("action-comment").fill(comment)
    page.get_by_test_id(f"invoice-{action}-btn").click()
