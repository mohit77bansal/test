"""KYC review: per-document approve/reject with remarks, activation gated on mandatory docs,
and the inviting Vendor Manager can never review or activate (UI hidden and direct POST refused)."""
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from playwright.sync_api import expect

from .conftest import emails_to, login, logout

PAN = "AAACK2468K"
EMAIL = "kyc@vendor.test"
GOODS_MANDATORY = ["PAN", "GST_CERT", "CANCELLED_CHEQUE"]


def _pdf(name, **fields):
    marker = f"FAKE_PARSE:{json.dumps(fields)}:END".encode()
    return SimpleUploadedFile(name, b"%PDF-1.4\n" + marker + b"\n%%EOF", content_type="application/pdf")


def submitted_vendor(seed, inviter="vm"):
    """A GOODS vendor invited by `inviter`, registration complete, all mandatory docs uploaded, SUBMITTED."""
    from apps.parsing.validators import make_gstin
    from apps.vendors import services
    from apps.vendors.models import DocumentType

    gstin = make_gstin("27", PAN)
    vendor, token = services.invite_vendor(by=seed[inviter], legal_name="Kay Goods Private Limited", email=EMAIL, category="GOODS")
    user = services.accept_invite(services.find_invite(token), password="Vend0r-Passw0rd!")
    vendor.refresh_from_db()
    services.save_registration(vendor, by=user, data={
        "legal_name": "Kay Goods Private Limited", "trade_name": "Kay Goods", "constitution": "PRIVATE_LTD", "pan": PAN, "gstin": gstin,
        "address": "4 Ring Road, Mumbai", "contact_name": "Kiran Kay", "contact_phone": "9800011122", "msme_status": "NOT_MSME",
        "udyam_no": "", "msme_type": "", "holder_name": "Kay Goods Private Limited", "account_no": "112233445566",
        "ifsc": "HDFC0004455", "bank_name": "HDFC Bank", "consent": True,
    })
    parsed = {
        "PAN": {"pan": PAN},
        "GST_CERT": {"gstin": gstin},
        "CANCELLED_CHEQUE": {"account_no": "112233445566", "ifsc": "HDFC0004455"},
    }
    for code in GOODS_MANDATORY:
        services.upload_document(vendor, by=user, document_type=DocumentType.objects.get(code=code), uploaded=_pdf(f"{code}.pdf", **parsed[code]))
    services.submit_registration(vendor, by=user)
    vendor.refresh_from_db()
    assert vendor.status == "SUBMITTED"
    return vendor


def _doc_status(page, code):
    return page.get_by_test_id(f"doc-status-{code}").get_by_test_id("status-badge")


def _vendor_status(page):
    return page.get_by_test_id("vendor-status").get_by_test_id("status-badge")


def _post(page, path, data=None):
    """Direct form POST with the browser's session + CSRF cookie (bypasses the hidden UI)."""
    token = next(c["value"] for c in page.context.cookies() if c["name"] == "csrftoken")
    return page.request.post(path, form=data or {}, headers={"X-CSRFToken": token, "Referer": page.url}, max_redirects=0)


def test_per_document_approve_and_reject_with_remarks(page, seed):
    vendor = submitted_vendor(seed)
    login(page, "vm2@test.local")
    page.goto(f"/console/vendors/{vendor.pk}/")
    expect(page.get_by_test_id("vendor-review-panel")).to_be_visible()
    expect(_vendor_status(page)).to_have_attribute("data-status", "SUBMITTED")
    for code in GOODS_MANDATORY:
        expect(_doc_status(page, code)).to_have_attribute("data-status", "UPLOADED")

    # Rejecting needs remarks.
    page.get_by_test_id("doc-reject-btn-GST_CERT").click()
    expect(page.get_by_test_id("flash-error")).to_be_visible()
    expect(_doc_status(page, "GST_CERT")).to_have_attribute("data-status", "UPLOADED")

    page.get_by_test_id("doc-remarks-GST_CERT").fill("Certificate is for a different address")
    page.get_by_test_id("doc-reject-btn-GST_CERT").click()
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    expect(_doc_status(page, "GST_CERT")).to_have_attribute("data-status", "REJECTED")
    expect(page.get_by_test_id("doc-status-GST_CERT")).to_contain_text("Certificate is for a different address")
    # First review action moves the vendor to Under review.
    expect(_vendor_status(page)).to_have_attribute("data-status", "UNDER_REVIEW")

    page.get_by_test_id("doc-remarks-PAN").fill("Matches the form")
    page.get_by_test_id("doc-approve-btn-PAN").click()
    expect(_doc_status(page, "PAN")).to_have_attribute("data-status", "APPROVED")
    expect(_doc_status(page, "CANCELLED_CHEQUE")).to_have_attribute("data-status", "UPLOADED")

    from apps.vendors.models import VendorDocument

    gst = VendorDocument.objects.get(vendor=vendor, document_type__code="GST_CERT", is_current=True)
    assert (gst.status, gst.remarks, gst.reviewer.email) == ("REJECTED", "Certificate is for a different address", "vm2@test.local")

    # After send back the vendor sees the per-document remarks.
    page.get_by_test_id("sendback-remarks").fill("Fix the GST certificate")
    page.get_by_test_id("vendor-sendback-btn").click()
    expect(_vendor_status(page)).to_have_attribute("data-status", "DRAFT")
    logout(page)
    login(page, EMAIL, "Vend0r-Passw0rd!")
    page.goto("/vendor/onboarding/documents/")
    expect(page.get_by_test_id("review-remarks")).to_contain_text("Fix the GST certificate")
    expect(page.get_by_test_id("doc-remarks-GST_CERT")).to_contain_text("Certificate is for a different address")


def test_activation_blocked_until_mandatory_documents_approved(page, seed):
    vendor = submitted_vendor(seed)
    login(page, "vm2@test.local")
    page.goto(f"/console/vendors/{vendor.pk}/")

    # Nothing approved yet.
    page.get_by_test_id("vendor-activate-btn").click()
    expect(page.get_by_test_id("flash-error")).to_be_visible()
    expect(_vendor_status(page)).to_have_attribute("data-status", "SUBMITTED")

    # Two of three mandatory docs approved: still blocked.
    for code in GOODS_MANDATORY[:2]:
        page.get_by_test_id(f"doc-approve-btn-{code}").click()
        expect(_doc_status(page, code)).to_have_attribute("data-status", "APPROVED")
    page.get_by_test_id("vendor-activate-btn").click()
    expect(page.get_by_test_id("flash-error")).to_contain_text("Cancelled cheque")
    expect(_vendor_status(page)).to_have_attribute("data-status", "UNDER_REVIEW")
    assert emails_to(EMAIL, "Welcome") == []

    # A rejected mandatory doc also blocks.
    page.get_by_test_id("doc-remarks-CANCELLED_CHEQUE").fill("Not cancelled")
    page.get_by_test_id("doc-reject-btn-CANCELLED_CHEQUE").click()
    page.get_by_test_id("vendor-activate-btn").click()
    expect(page.get_by_test_id("flash-error")).to_be_visible()
    expect(_vendor_status(page)).to_have_attribute("data-status", "UNDER_REVIEW")

    # Optional MSME doc is not needed: approving the cheque is enough.
    page.get_by_test_id("doc-approve-btn-CANCELLED_CHEQUE").click()
    expect(_doc_status(page, "CANCELLED_CHEQUE")).to_have_attribute("data-status", "APPROVED")
    page.get_by_test_id("vendor-activate-btn").click()
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    expect(_vendor_status(page)).to_have_attribute("data-status", "ACTIVE")
    expect(page.get_by_test_id("vendor-review-panel")).to_have_count(0)
    assert len(emails_to(EMAIL, "Welcome")) == 1

    vendor.refresh_from_db()
    assert vendor.status == "ACTIVE"
    assert vendor.active_bank_account is not None


def test_inviter_cannot_review_or_activate(page, seed):
    vendor = submitted_vendor(seed, inviter="vm")
    login(page, "vm@test.local")
    page.goto(f"/console/vendors/{vendor.pk}/")
    expect(page.get_by_test_id("inviter-notice")).to_be_visible()
    expect(page.get_by_test_id("vendor-review-panel")).to_have_count(0)
    expect(page.get_by_test_id("vendor-activate-btn")).to_have_count(0)
    for code in GOODS_MANDATORY:
        expect(page.get_by_test_id(f"doc-approve-btn-{code}")).to_have_count(0)
    # The inviter's queue does not list the vendor for KYC.
    page.goto("/console/")
    expect(page.get_by_test_id(f"kyc-row-{vendor.vendor_code}")).to_have_count(0)

    # Direct POSTs are refused: document review ...
    from apps.vendors.models import Vendor, VendorDocument

    pan = VendorDocument.objects.get(vendor=vendor, document_type__code="PAN", is_current=True)
    _post(page, f"/console/vendors/{vendor.pk}/documents/{pan.pk}/review/", {"decision": "approve"})
    pan.refresh_from_db()
    assert pan.status == "UPLOADED"

    # ... and activation, even once every mandatory doc has been approved by another VM.
    from apps.vendors import services

    for d in VendorDocument.objects.filter(vendor=vendor, is_current=True):
        services.review_document(d, by=seed["vm2"], approve=True)
    resp = _post(page, f"/console/vendors/{vendor.pk}/activate/")
    assert resp.status == 302
    page.goto(f"/console/vendors/{vendor.pk}/")
    # Both refusals (review + activate) are flashed.
    expect(page.get_by_test_id("flash-error")).to_have_count(2)
    expect(_vendor_status(page)).to_have_attribute("data-status", "UNDER_REVIEW")
    assert Vendor.objects.get(pk=vendor.pk).status == "UNDER_REVIEW"
    assert emails_to(EMAIL, "Welcome") == []

    # Send back is refused too.
    _post(page, f"/console/vendors/{vendor.pk}/send-back/", {"remarks": "self review"})
    assert Vendor.objects.get(pk=vendor.pk).status == "UNDER_REVIEW"

    # A different VM can activate.
    logout(page)
    login(page, "vm2@test.local")
    page.goto(f"/console/vendors/{vendor.pk}/")
    expect(page.get_by_test_id("inviter-notice")).to_have_count(0)
    page.get_by_test_id("vendor-activate-btn").click()
    expect(_vendor_status(page)).to_have_attribute("data-status", "ACTIVE")
