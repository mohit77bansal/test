"""Invoice upload + parse: 'Reading your invoice' state, pre-filled fields, amber low-confidence field,
edit + submit; duplicate / GSTIN / totals / size / type validations."""
import re
from datetime import date, timedelta

from playwright.sync_api import expect

from .conftest import fake_invoice_pdf, invoice_fields, login, logout, select_containing, upload_and_submit


def _amount(value):
    """Decimal inputs may render as 50000 or 50000.00."""
    return re.compile(rf"^{value}(\.0+)?$")


def _upload(page, path):
    page.goto("/vendor/invoices/upload/")
    page.get_by_test_id("invoice-file").set_input_files(path)
    page.get_by_test_id("invoice-upload-btn").click()


def _upload_parsed(page, tmp_path, name, fields):
    _upload(page, fake_invoice_pdf(tmp_path / f"{name}.pdf", **fields))
    page.get_by_test_id("invoice-submit-btn").wait_for()


def _submit_form(page, cost_centre="CC-OPS"):
    select_containing(page.get_by_test_id("cost_centre"), cost_centre)
    page.get_by_test_id("invoice-submit-btn").click()


def _invoices(vendor):
    from apps.invoices.models import Invoice

    return Invoice.objects.filter(vendor=vendor)


def test_upload_shows_reading_then_prefilled_form_with_amber_field(page, seed, tmp_path, monkeypatch):
    from apps.invoices.models import Invoice
    from apps.parsing import services as parsing
    from apps.parsing import tasks

    vendor = seed["vendor_a"]
    gstin = vendor.primary_gstin.gstin
    # Hold the parser so the 'reading' state is observable.
    monkeypatch.setattr(tasks.parse_file_task, "delay", lambda *a, **k: None)

    login(page, "vendora.user@test.local")
    _upload(page, fake_invoice_pdf(tmp_path / "parse-1.pdf", **invoice_fields(59000, gstin, "ACME-PARSE-1")))
    expect(page).to_have_url(re.compile(r"/vendor/invoices/[0-9a-f-]+/edit/$"))
    expect(page.get_by_test_id("parse-reading")).to_be_visible()
    expect(page.get_by_test_id("invoice-submit-btn")).to_have_count(0)

    inv = Invoice.objects.get(vendor=vendor, status="DRAFT")
    assert inv.parse_job.status == "QUEUED"
    parsing.run_job(inv.parse_job)

    # The 2-second HTMX poll swaps the form in without a reload.
    expect(page.get_by_test_id("invoice-form")).to_be_visible(timeout=10_000)
    expect(page.get_by_test_id("parse-reading")).to_have_count(0)

    expect(page.get_by_test_id("supplier_gstin")).to_have_value(gstin)
    expect(page.get_by_test_id("invoice_no")).to_have_value("ACME-PARSE-1")
    expect(page.get_by_test_id("invoice_date")).to_have_value(date.today().isoformat())
    expect(page.get_by_test_id("taxable")).to_have_value(_amount("50000"))
    expect(page.get_by_test_id("cgst")).to_have_value(_amount("4500"))
    expect(page.get_by_test_id("sgst")).to_have_value(_amount("4500"))
    expect(page.get_by_test_id("total")).to_have_value(_amount("59000"))

    # invoice_date is deliberately low-confidence in the fake parser -> amber.
    expect(page.get_by_test_id("low-confidence-notice")).to_be_visible()
    expect(page.get_by_test_id("invoice_date")).to_have_attribute("data-low-confidence", "true")
    expect(page.get_by_test_id("low-confidence-invoice_date")).to_be_visible()
    for confident in ("supplier_gstin", "invoice_no", "total"):
        expect(page.get_by_test_id(confident)).not_to_have_attribute("data-low-confidence", "true")
        expect(page.get_by_test_id(f"low-confidence-{confident}")).to_have_count(0)

    # Vendor edits the amber field and the number, then submits.
    edited_date = date.today() - timedelta(days=3)
    page.get_by_test_id("invoice_date").fill(edited_date.isoformat())
    page.get_by_test_id("invoice_no").fill("ACME-PARSE-1A")
    _submit_form(page)
    expect(page.get_by_test_id("invoice-ref")).to_have_text(re.compile(r"^INV-\d{4}-\d{5}$"))
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "FIN_PRECHECK")

    inv.refresh_from_db()
    assert (inv.invoice_no, inv.invoice_date, str(inv.total)) == ("ACME-PARSE-1A", edited_date, "59000.00")


def test_duplicate_invoice_number_blocked(page, seed, tmp_path):
    vendor = seed["vendor_a"]
    gstin = vendor.primary_gstin.gstin
    login(page, "vendora@test.local")
    upload_and_submit(page, tmp_path, total=50000, gstin=gstin, invoice_no="ACME-DUP-1")

    # Same number again (case-insensitive) in the same FY.
    _upload_parsed(page, tmp_path, "dup-2", invoice_fields(12000, gstin, "acme-dup-1"))
    _submit_form(page)
    expect(page.get_by_test_id("error-invoice_no")).to_be_visible()
    expect(page.get_by_test_id("invoice-ref")).to_have_count(0)
    assert _invoices(vendor).filter(invoice_no__iexact="ACME-DUP-1").exclude(status="DRAFT").count() == 1

    # Duplicates are per vendor: vendor B may use the same number.
    logout(page)
    login(page, "vendorb@test.local")
    upload_and_submit(page, tmp_path, total=50000, gstin=seed["vendor_b"].primary_gstin.gstin, invoice_no="ACME-DUP-1")
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "FIN_PRECHECK")


def test_gstin_mismatch_blocked(page, seed, tmp_path):
    vendor = seed["vendor_a"]
    other = seed["vendor_b"].primary_gstin.gstin
    login(page, "vendora@test.local")
    _upload_parsed(page, tmp_path, "gstin-b", invoice_fields(50000, other, "ACME-GST-1"))
    expect(page.get_by_test_id("supplier_gstin")).to_have_value(other)
    _submit_form(page)
    expect(page.get_by_test_id("error-supplier_gstin")).to_be_visible()
    expect(page.get_by_test_id("invoice-ref")).to_have_count(0)
    assert not _invoices(vendor).exclude(status="DRAFT").exists()


def test_totals_mismatch_blocked(page, seed, tmp_path):
    vendor = seed["vendor_a"]
    gstin = vendor.primary_gstin.gstin
    fields = {**invoice_fields(50000, gstin, "ACME-TOT-1"), "total": "52000.00"}
    login(page, "vendora@test.local")
    _upload_parsed(page, tmp_path, "totals", fields)
    _submit_form(page)
    expect(page.get_by_test_id("error-total")).to_be_visible()
    expect(page.get_by_test_id("invoice-ref")).to_have_count(0)

    # Correcting the total lets it through.
    page.get_by_test_id("total").fill("50000.00")
    _submit_form(page)
    expect(page.get_by_test_id("status-badge")).to_have_attribute("data-status", "FIN_PRECHECK")


def test_file_over_10mb_rejected(page, seed, tmp_path):
    vendor = seed["vendor_a"]
    big = tmp_path / "big.pdf"
    big.write_bytes(b"%PDF-1.4\n" + b"0" * (10 * 1024 * 1024 + 1024) + b"\n%%EOF")
    login(page, "vendora@test.local")
    page.goto("/vendor/invoices/upload/")
    file_input = page.get_by_test_id("invoice-file")

    # Client-side check flags the file before upload.
    file_input.set_input_files(str(big))
    assert file_input.evaluate("el => el.validationMessage") != ""
    page.get_by_test_id("invoice-upload-btn").click()
    expect(page).to_have_url(re.compile(r"/vendor/invoices/upload/$"))
    assert not _invoices(vendor).exists()

    # Server-side check rejects it even when the client check is bypassed.
    file_input.evaluate("el => { el.removeAttribute('data-max-bytes'); el.setCustomValidity(''); }")
    file_input.set_input_files(str(big))
    assert file_input.evaluate("el => el.validationMessage") == ""
    page.get_by_test_id("invoice-upload-btn").click()
    expect(page.get_by_test_id("form-error")).to_contain_text("10 MB")
    expect(page).to_have_url(re.compile(r"/vendor/invoices/upload/$"))
    assert not _invoices(vendor).exists()


def test_wrong_file_type_rejected(page, seed, tmp_path):
    vendor = seed["vendor_a"]
    login(page, "vendora@test.local")
    txt = tmp_path / "invoice.txt"
    txt.write_text("this is not an invoice")
    _upload(page, str(txt))
    expect(page.get_by_test_id("form-error")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/vendor/invoices/upload/$"))

    # A renamed non-PDF is caught by content sniffing, not just the extension.
    fake = tmp_path / "renamed.pdf"
    fake.write_bytes(b"PK\x03\x04 zip pretending to be a pdf")
    _upload(page, str(fake))
    expect(page.get_by_test_id("form-error")).to_be_visible()
    assert not _invoices(vendor).exists()
