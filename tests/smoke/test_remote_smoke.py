import os
import secrets

import pytest
from playwright.sync_api import expect

from .conftest import env

pytestmark = pytest.mark.smoke


def login(page, email):
    page.goto("/login/")
    page.get_by_test_id("login-email").fill(email)
    page.get_by_test_id("login-password").fill(env("SMOKE_PASSWORD"))
    page.get_by_test_id("login-submit-btn").click()
    page.get_by_test_id("current-user").wait_for()


def logout(page):
    page.get_by_test_id("logout-btn").click()
    page.get_by_test_id("login-email").wait_for()


def test_remote_smoke_login_upload_parse_l1_approve(page, tmp_path):
    cost_centre = env("SMOKE_COST_CENTRE", "CC-OPS")
    sample = os.environ.get("SMOKE_INVOICE_FILE")
    if not sample:  # unique bytes so the invoice number from the fake parser is unique per run
        sample = tmp_path / "smoke.pdf"
        sample.write_bytes(b"%PDF-1.4\n% smoke " + secrets.token_hex(8).encode() + b"\n%%EOF")

    login(page, env("SMOKE_VENDOR_EMAIL"))
    page.goto("/vendor/invoices/upload/")
    page.get_by_test_id("invoice-file").set_input_files(str(sample))
    page.get_by_test_id("invoice-upload-btn").click()
    page.get_by_test_id("invoice-submit-btn").wait_for(timeout=60_000)  # parse finished (HTMX poll)
    no = page.get_by_test_id("invoice_no")
    no.fill(f"SMOKE-{secrets.token_hex(4).upper()}")
    cc = page.get_by_test_id("cost_centre")
    cc.select_option(cc.evaluate("(el, t) => [...el.options].find(o => o.text.includes(t)).value", cost_centre))
    page.get_by_test_id("invoice-submit-btn").click()
    ref = page.get_by_test_id("invoice-ref").inner_text()
    logout(page)

    login(page, env("SMOKE_FINANCE_EMAIL"))
    page.goto("/console/")
    page.get_by_test_id(f"queue-open-{ref}").click()
    page.get_by_test_id("tds_section").select_option("NA")
    page.get_by_test_id("invoice-approve-btn").click()
    logout(page)

    login(page, env("SMOKE_L1_EMAIL"))
    page.goto("/console/")
    page.get_by_test_id(f"queue-open-{ref}").click()
    page.get_by_test_id("invoice-approve-btn").click()
    page.goto(f"/console/invoices/?q={ref}")
    expect(page.get_by_test_id(f"invoice-row-{ref}").get_by_test_id("status-badge")).to_have_attribute("data-status", "APPROVED")
