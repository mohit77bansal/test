"""Vendor onboarding end to end: invite -> magic link -> registration + KYC (with parse pre-fill) -> submit
-> send back by a different VM -> fix + resubmit -> approve docs + activate -> welcome email. No email back-and-forth."""
import re

from django.conf import settings
from playwright.sync_api import expect

from .conftest import emails_to, fake_invoice_pdf, login, logout, select_containing

NEW_EMAIL = "owner@newco.test"
NEW_NAME = "Newco Services Private Limited"
NEW_PAN = "AAACN4321N"
ACCOUNT_NO = "918273645500"
IFSC = "ICIC0004321"
PW_NEW = "Vend0r-Passw0rd!"
MANDATORY_SERVICES = ["PAN", "GST_CERT", "CANCELLED_CHEQUE", "AGREEMENT"]


def _link_path(message):
    site = settings.SITE_URL.rstrip("/")
    body = message.body + "".join(c for c, _ in getattr(message, "alternatives", []))
    m = re.search(re.escape(site) + r"(/[^\s\"'<>]*)", body)
    assert m, f"No {site} link in email {message.subject!r}"
    return m.group(1)


def _gstin():
    from apps.parsing.validators import make_gstin

    return make_gstin("27", NEW_PAN)


def _upload_doc(page, tmp_path, code, valid_to=None, **parsed):
    path = fake_invoice_pdf(tmp_path / f"{code.lower()}-{len(list(tmp_path.iterdir()))}.pdf", **parsed)
    page.get_by_test_id(f"doc-upload-{code}").set_input_files(path)
    if valid_to:
        page.get_by_test_id(f"doc-valid-to-{code}").fill(valid_to)
    page.get_by_test_id(f"doc-upload-btn-{code}").click()
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    expect(page.get_by_test_id(f"doc-status-{code}").get_by_test_id("status-badge")).to_have_attribute("data-status", "UPLOADED")


def _vendor():
    from apps.vendors.models import Vendor

    return Vendor.objects.get(contact_email=NEW_EMAIL)


def test_invite_to_active_without_email_back_and_forth(page, seed, tmp_path):
    gstin = _gstin()

    # 1. VM invites the vendor.
    login(page, "vm@test.local")
    page.goto("/console/vendors/")
    page.get_by_test_id("vendor-invite-link").click()
    page.get_by_test_id("legal_name").fill(NEW_NAME)
    page.get_by_test_id("email").fill(NEW_EMAIL)
    page.get_by_test_id("category").select_option("SERVICES")
    page.get_by_test_id("invite-submit-btn").click()
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    expect(page.get_by_test_id("vendor-status").get_by_test_id("status-badge")).to_have_attribute("data-status", "INVITED")
    vendor = _vendor()
    code = vendor.vendor_code
    logout(page)

    invites = emails_to(NEW_EMAIL, "Invitation to register")
    assert len(invites) == 1
    assert "vm@test.local" in invites[0].cc
    invite_path = _link_path(invites[0])
    assert invite_path.startswith("/vendor/invite/")

    # 2. Magic link -> set password -> lands on the registration form.
    page.goto(invite_path)
    page.get_by_test_id("invite-password1").fill(PW_NEW)
    page.get_by_test_id("invite-password2").fill(PW_NEW)
    page.get_by_test_id("invite-accept-btn").click()
    expect(page).to_have_url(re.compile(r"/vendor/onboarding/$"))
    expect(page.get_by_test_id("registration-save-btn")).to_be_visible()
    expect(page.get_by_test_id("status-badge").first).to_have_attribute("data-status", "DRAFT")

    # 3. KYC: upload PAN, GST certificate and cheque; the fake parser reads them.
    page.get_by_test_id("step-documents").click()
    _upload_doc(page, tmp_path, "PAN", pan=NEW_PAN, name=NEW_NAME)
    expect(page.get_by_test_id("doc-parse-PAN")).to_contain_text(NEW_PAN)
    _upload_doc(page, tmp_path, "GST_CERT", gstin=gstin, legal_name=NEW_NAME, trade_name="Newco", address="12 MG Road, Pune, Maharashtra")
    expect(page.get_by_test_id("doc-parse-GST_CERT")).to_contain_text(gstin)
    _upload_doc(page, tmp_path, "CANCELLED_CHEQUE", account_no=ACCOUNT_NO, ifsc=IFSC, holder_name=NEW_NAME, bank_name="ICICI Bank")
    expect(page.get_by_test_id("doc-parse-CANCELLED_CHEQUE")).to_contain_text(ACCOUNT_NO)

    # Back on the registration form the parsed values are pre-filled and flagged.
    page.get_by_test_id("step-profile").click()
    expect(page.get_by_test_id("prefill-notice")).to_be_visible()
    for field, value in [("pan", NEW_PAN), ("gstin", gstin), ("account_no", ACCOUNT_NO), ("ifsc", IFSC), ("trade_name", "Newco")]:
        expect(page.get_by_test_id(f"prefilled-{field}")).to_be_visible()
        expect(page.get_by_test_id(field)).to_have_value(value)
    expect(page.get_by_test_id("legal_name")).to_have_value(NEW_NAME)

    # Complete and save the registration form.
    page.get_by_test_id("constitution").select_option("PRIVATE_LTD")
    page.get_by_test_id("contact_name").fill("Neha Newco")
    page.get_by_test_id("contact_phone").fill("9811122233")
    page.get_by_test_id("msme_status").select_option("NOT_MSME")
    page.get_by_test_id("consent").check()
    page.get_by_test_id("registration-save-btn").click()
    expect(page).to_have_url(re.compile(r"/vendor/onboarding/documents/$"))
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    expect(page.get_by_test_id("parse-mismatch")).to_have_count(0)

    # Remaining mandatory doc (agreement, has expiry), then submit.
    _upload_doc(page, tmp_path, "AGREEMENT", valid_to="2028-03-31")
    page.get_by_test_id("onboarding-submit-btn").click()
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    expect(page.get_by_test_id("awaiting-review")).to_be_visible()
    assert len(emails_to("vm@test.local", "New vendor awaiting KYC review")) == 1
    logout(page)

    # 4. The inviter cannot review.
    login(page, "vm@test.local")
    page.goto(f"/console/vendors/{vendor.pk}/")
    expect(page.get_by_test_id("inviter-notice")).to_be_visible()
    expect(page.get_by_test_id("vendor-review-panel")).to_have_count(0)
    expect(page.get_by_test_id("doc-approve-btn-PAN")).to_have_count(0)
    logout(page)

    # 5. A different VM rejects the cheque and sends the registration back.
    login(page, "vm2@test.local")
    page.goto("/console/")
    expect(page.get_by_test_id(f"kyc-row-{code}")).to_be_visible()
    page.goto(f"/console/vendors/{vendor.pk}/")
    expect(page.get_by_test_id("inviter-notice")).to_have_count(0)
    page.get_by_test_id("doc-approve-btn-PAN").click()
    expect(page.get_by_test_id("doc-status-PAN").get_by_test_id("status-badge")).to_have_attribute("data-status", "APPROVED")
    expect(page.get_by_test_id("vendor-status").get_by_test_id("status-badge")).to_have_attribute("data-status", "UNDER_REVIEW")
    page.get_by_test_id("doc-remarks-CANCELLED_CHEQUE").fill("Cheque image is blurred")
    page.get_by_test_id("doc-reject-btn-CANCELLED_CHEQUE").click()
    expect(page.get_by_test_id("doc-status-CANCELLED_CHEQUE").get_by_test_id("status-badge")).to_have_attribute("data-status", "REJECTED")
    page.get_by_test_id("sendback-remarks").fill("Please upload a clearer cancelled cheque")
    page.get_by_test_id("vendor-sendback-btn").click()
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    expect(page.get_by_test_id("vendor-status").get_by_test_id("status-badge")).to_have_attribute("data-status", "DRAFT")
    logout(page)
    sent_back = emails_to(NEW_EMAIL, "Action needed on your registration")
    assert len(sent_back) == 1
    assert "Please upload a clearer cancelled cheque" in sent_back[0].body

    # 6. Vendor sees the remarks, re-uploads the rejected doc and resubmits.
    login(page, NEW_EMAIL, PW_NEW)
    page.goto("/vendor/onboarding/documents/")
    expect(page.get_by_test_id("review-remarks")).to_contain_text("Please upload a clearer cancelled cheque")
    expect(page.get_by_test_id("doc-remarks-CANCELLED_CHEQUE")).to_contain_text("Cheque image is blurred")
    expect(page.get_by_test_id("doc-status-PAN").get_by_test_id("status-badge")).to_have_attribute("data-status", "APPROVED")
    _upload_doc(page, tmp_path, "CANCELLED_CHEQUE", account_no=ACCOUNT_NO, ifsc=IFSC, holder_name=NEW_NAME, bank_name="ICICI Bank")
    expect(page.get_by_test_id("doc-remarks-CANCELLED_CHEQUE")).to_have_count(0)
    page.get_by_test_id("onboarding-submit-btn").click()
    expect(page.get_by_test_id("awaiting-review")).to_be_visible()
    logout(page)

    # 7. VM2 approves the documents and activates.
    login(page, "vm2@test.local")
    page.goto(f"/console/vendors/{vendor.pk}/")
    for doc in MANDATORY_SERVICES:
        if page.get_by_test_id(f"doc-status-{doc}").get_by_test_id("status-badge").get_attribute("data-status") != "APPROVED":
            page.get_by_test_id(f"doc-approve-btn-{doc}").click()
            expect(page.get_by_test_id(f"doc-status-{doc}").get_by_test_id("status-badge")).to_have_attribute("data-status", "APPROVED")
    page.get_by_test_id("vendor-activate-btn").click()
    expect(page.get_by_test_id("flash-success")).to_be_visible()
    expect(page.get_by_test_id("vendor-status").get_by_test_id("status-badge")).to_have_attribute("data-status", "ACTIVE")
    logout(page)

    welcome = emails_to(NEW_EMAIL, "Welcome to the Solfin Vendor Portal")
    assert len(welcome) == 1
    assert code in welcome[0].subject
    assert "vm@test.local" in welcome[0].cc

    # 8. The vendor now lands on the dashboard and can upload invoices.
    login(page, NEW_EMAIL, PW_NEW)
    expect(page.get_by_test_id("vendor-dashboard")).to_be_visible()
    expect(page.get_by_test_id("invoice-upload-link")).to_be_visible()


def test_invite_link_is_single_use(page, seed):
    from apps.accounts.models import User
    from apps.vendors import services

    _, token = services.invite_vendor(by=seed["vm"], legal_name=NEW_NAME, email=NEW_EMAIL, category="GOODS")
    path = _link_path(emails_to(NEW_EMAIL, "Invitation to register")[0])
    assert path == f"/vendor/invite/{token}/"
    page.goto(path)
    page.get_by_test_id("invite-password1").fill(PW_NEW)
    page.get_by_test_id("invite-password2").fill(PW_NEW)
    page.get_by_test_id("invite-accept-btn").click()
    expect(page.get_by_test_id("registration-save-btn")).to_be_visible()
    assert User.objects.filter(email=NEW_EMAIL).exists()
    logout(page)
    page.goto(path)
    expect(page.get_by_test_id("invite-invalid")).to_be_visible()


def test_registration_rejects_gstin_without_pan(page, seed):
    from apps.vendors import services

    services.invite_vendor(by=seed["vm"], legal_name=NEW_NAME, email=NEW_EMAIL, category="GOODS")
    page.goto(_link_path(emails_to(NEW_EMAIL, "Invitation to register")[0]))
    page.get_by_test_id("invite-password1").fill(PW_NEW)
    page.get_by_test_id("invite-password2").fill(PW_NEW)
    page.get_by_test_id("invite-accept-btn").click()
    page.get_by_test_id("pan").fill(NEW_PAN)
    page.get_by_test_id("gstin").fill(seed["vendor_a"].primary_gstin.gstin)  # valid GSTIN, different PAN
    page.get_by_test_id("constitution").select_option("PRIVATE_LTD")
    page.get_by_test_id("contact_name").fill("Neha Newco")
    page.get_by_test_id("contact_phone").fill("9811122233")
    page.get_by_test_id("address").fill("12 MG Road, Pune")
    select_containing(page.get_by_test_id("msme_status"), "Not MSME")
    page.get_by_test_id("holder_name").fill(NEW_NAME)
    page.get_by_test_id("account_no").fill(ACCOUNT_NO)
    page.get_by_test_id("ifsc").fill(IFSC)
    page.get_by_test_id("consent").check()
    page.get_by_test_id("registration-save-btn").click()
    expect(page.get_by_test_id("error-gstin")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/vendor/onboarding/$"))
