import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError

from apps.parsing.validators import make_gstin
from apps.vendors import services
from apps.vendors.models import DocumentType, Vendor, VendorBankAccount, VendorDocument
from tests.conftest import fake_pdf

PAN = "AAACZ9999Z"


def _register(vendor, user):
    services.save_registration(vendor, by=user, data={
        "legal_name": "Zeta Services Private Limited", "trade_name": "Zeta", "constitution": "PRIVATE_LTD", "pan": PAN,
        "gstin": make_gstin("27", PAN), "address": "Pune", "contact_name": "Zed", "contact_phone": "9800000001",
        "msme_status": "NOT_MSME", "udyam_no": "", "msme_type": "", "holder_name": "Zeta Services", "account_no": "123456789012",
        "ifsc": "HDFC0001234", "bank_name": "HDFC", "consent": True,
    })
    for dt in vendor.mandatory_document_types():
        services.upload_document(vendor, by=user, document_type=dt, uploaded=fake_pdf(f"{dt.code}.pdf"))


@pytest.fixture
def invited(users, run_on_commit):
    vendor, token = services.invite_vendor(by=users["vm"], legal_name="Zeta Services", email="zeta@example.com", category="SERVICES")
    invite = services.find_invite(token)
    user = services.accept_invite(invite, password="Str0ng-Passw0rd!")
    vendor.refresh_from_db()
    return vendor, user


def test_invite_email_and_draft(invited):
    vendor, user = invited
    assert vendor.status == Vendor.DRAFT
    assert vendor.vendor_code.startswith("V")
    invite_mail = [m for m in mail.outbox if "Invitation" in m.subject][0]
    assert invite_mail.to == ["zeta@example.com"] and "vm@test.local" in invite_mail.cc
    assert "/vendor/invite/" in invite_mail.body and "Signed agreement" in invite_mail.body
    assert user.has_role("VENDOR_ADMIN")


def test_invite_link_single_use(users, run_on_commit):
    _, token = services.invite_vendor(by=users["vm"], legal_name="Once Ltd", email="once@example.com", category="GOODS")
    invite = services.find_invite(token)
    services.accept_invite(invite, password="Str0ng-Passw0rd!")
    with pytest.raises(ValidationError):
        services.accept_invite(services.find_invite(token), password="Str0ng-Passw0rd!")


def test_full_onboarding_to_active(invited, users):
    vendor, user = invited
    _register(vendor, user)
    services.submit_registration(vendor, by=user)
    assert vendor.status == Vendor.SUBMITTED
    assert any("awaiting KYC review" in m.subject for m in mail.outbox)
    reviewer = users["vm2"]
    for doc in services.current_documents(vendor).values():
        services.review_document(doc, by=reviewer, approve=True)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.UNDER_REVIEW
    services.activate(vendor, by=reviewer)
    vendor.refresh_from_db()
    assert vendor.status == Vendor.ACTIVE
    assert vendor.bank_accounts.get().status == VendorBankAccount.ACTIVE
    assert any("Welcome" in m.subject and user.email in m.to for m in mail.outbox)


def test_parser_prefill_and_mismatch(invited):
    vendor, user = invited
    pan_type = DocumentType.objects.get(code="PAN")
    services.upload_document(vendor, by=user, document_type=pan_type, uploaded=fake_pdf("pan.pdf", pan="AAACZ9999Z", name="Zeta Services Private Limited"))
    assert services.parsed_values(vendor)["pan"] == "AAACZ9999Z"
    _register(vendor, user)
    services.upload_document(vendor, by=user, document_type=pan_type, uploaded=fake_pdf("pan2.pdf", pan="AAACY1111Y"))
    assert [m["field"] for m in services.mismatches(vendor)] == ["pan"]


def test_submit_requires_mandatory_documents(invited):
    vendor, user = invited
    with pytest.raises(ValidationError) as e:
        services.submit_registration(vendor, by=user)
    assert any("Upload" in m for m in e.value.messages)


def test_inviter_cannot_review_or_activate(invited, users):
    vendor, user = invited
    _register(vendor, user)
    services.submit_registration(vendor, by=user)
    doc = next(iter(services.current_documents(vendor).values()))
    with pytest.raises(PermissionDenied):
        services.review_document(doc, by=users["vm"], approve=True)
    with pytest.raises(PermissionDenied):
        services.activate(vendor, by=users["vm"])


def test_activation_blocked_until_mandatory_docs_approved(invited, users):
    vendor, user = invited
    _register(vendor, user)
    services.submit_registration(vendor, by=user)
    docs = list(services.current_documents(vendor).values())
    services.review_document(docs[0], by=users["vm2"], approve=True)
    with pytest.raises(ValidationError):
        services.activate(vendor, by=users["vm2"])


def test_send_back_and_resubmit(invited, users):
    vendor, user = invited
    _register(vendor, user)
    services.submit_registration(vendor, by=user)
    doc = vendor.documents.get(document_type__code="PAN", is_current=True)
    with pytest.raises(ValidationError):
        services.review_document(doc, by=users["vm2"], approve=False, remarks="")
    services.review_document(doc, by=users["vm2"], approve=False, remarks="Blurred scan")
    services.send_back(vendor, by=users["vm2"], remarks="Please re-upload the PAN")
    vendor.refresh_from_db()
    assert vendor.status == Vendor.DRAFT
    sent_back = [m for m in mail.outbox if "Action needed" in m.subject][0]
    assert "Blurred scan" in sent_back.body
    with pytest.raises(ValidationError):  # rejected doc must be replaced first
        services.submit_registration(vendor, by=user)
    services.upload_document(vendor, by=user, document_type=doc.document_type, uploaded=fake_pdf("pan-new.pdf"))
    services.submit_registration(vendor, by=user)
    assert VendorDocument.objects.get(vendor=vendor, document_type__code="PAN", is_current=True).status == VendorDocument.UPLOADED


def test_bank_change_needs_finance_and_not_requester(seed, users, run_on_commit):
    vendor = seed["vendor_a"]
    va = users["vendora"]
    acct = services.request_bank_change(vendor, by=va, holder_name="Acme", account_no="999988887777", ifsc="ICIC0000001", bank_name="ICICI")
    assert vendor.has_pending_bank_change
    with pytest.raises(PermissionDenied):
        services.decide_bank_change(acct, by=users["vm"], approve=True)
    services.decide_bank_change(acct, by=users["finance"], approve=True)
    assert not vendor.has_pending_bank_change
    assert vendor.active_bank_account.account_last4 == "7777"
    assert vendor.bank_accounts.filter(status=VendorBankAccount.SUPERSEDED).count() == 1


def test_block_and_unblock(seed, users, run_on_commit):
    vendor = seed["vendor_a"]
    services.block(vendor, by=users["vm"], reason="Compliance hold")
    assert vendor.status == Vendor.BLOCKED
    assert any("blocked" in m.subject for m in mail.outbox)
    services.unblock(vendor, by=users["vm"])
    assert vendor.status == Vendor.ACTIVE


def test_sensitive_fields_are_encrypted(seed):
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("select pan_enc from vendors where id = %s", [seed["vendor_a"].pk])
        raw = cur.fetchone()[0]
    assert "AAACA1234A" not in raw and seed["vendor_a"].pan == "AAACA1234A"
