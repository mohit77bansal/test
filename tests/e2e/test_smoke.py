"""@smoke: runs after every deploy to UAT and prod (login, upload + parse, L1 approve)."""
import pytest
from playwright.sync_api import expect

from .conftest import act_on, login, logout, upload_and_submit

pytestmark = pytest.mark.smoke


def test_smoke_login_upload_parse_l1_approve(page, seed, tmp_path):
    gstin = seed["vendor_a"].primary_gstin.gstin
    login(page, "vendora@test.local")
    ref = upload_and_submit(page, tmp_path, total=50000, gstin=gstin, invoice_no="SMOKE-1")
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "FIN_PRECHECK")
    logout(page)

    login(page, "finance@test.local")
    act_on(page, ref, tds_section="194J")
    logout(page)

    login(page, "l1@test.local")
    act_on(page, ref)
    page.goto("/console/invoices/")
    expect(page.get_by_test_id(f"invoice-row-{ref}").get_by_test_id("status-badge")).to_have_attribute("data-status", "APPROVED")
