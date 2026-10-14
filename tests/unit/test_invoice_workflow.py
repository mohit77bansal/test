from datetime import date
from decimal import Decimal

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError

from apps.core.models import CostCentre
from apps.invoices import services
from apps.invoices.models import ApprovalMatrix, Invoice
from apps.vendors import services as vendor_services
from tests.conftest import fake_pdf

S = Invoice.Status
FIN = {"tds_section": "194J", "tds_amount": Decimal("0")}


def approve_chain(inv, users, levels):
    services.act(inv, by=users["finance"], action="APPROVE", fin_data=FIN)
    for lvl in levels:
        inv.refresh_from_db()
        assert inv.status == f"PENDING_{lvl}"
        assert inv in services.queue_for(users[lvl.lower()])
        services.act(inv, by=users[lvl.lower()], action="APPROVE")
    inv.refresh_from_db()
    return inv


def test_50k_stops_at_l1(make_invoice, users):
    inv = make_invoice(total=50000)
    assert inv.status == S.FIN_PRECHECK and inv.required_level == "L1"
    assert inv in services.queue_for(users["finance"])
    inv = approve_chain(inv, users, ["L1"])
    assert inv.status == S.APPROVED and inv.due_date is not None
    assert any("approved" in m.subject and "vendora@test.local" in m.to for m in mail.outbox)


def test_15_lakh_goes_to_l3(make_invoice, users):
    inv = make_invoice(total=1500000)
    assert inv.required_level == "L3"
    mail.outbox.clear()
    inv = approve_chain(inv, users, ["L1", "L2", "L3"])
    assert inv.status == S.APPROVED
    pending = [m for m in mail.outbox if "awaiting your" in m.subject]
    assert [m.to for m in pending] == [["l1@test.local"], ["l2@test.local"], ["l3@test.local"]]


def test_submit_emails_received_with_reference(make_invoice):
    inv = make_invoice(total=50000)
    m = [m for m in mail.outbox if "received" in m.subject][0]
    assert inv.portal_ref in m.subject and "ap@solfin.co.in" in m.cc


def test_validations(seed, users, run_on_commit):
    vendor, va = seed["vendor_a"], users["vendora"]
    inv = services.create_from_upload(vendor, by=va, uploaded=fake_pdf())
    good = {"supplier_gstin": vendor.primary_gstin.gstin, "invoice_no": "X-1", "invoice_date": date.today(), "cost_centre": seed["cost_centre"],
            "taxable": Decimal("100"), "cgst": Decimal("9"), "sgst": Decimal("9"), "igst": Decimal("0"), "total": Decimal("118")}
    for bad, field in [({"supplier_gstin": seed["vendor_b"].primary_gstin.gstin}, "supplier_gstin"), ({"total": Decimal("125")}, "total"), ({"cost_centre": None}, "cost_centre")]:
        with pytest.raises(ValidationError) as e:
            services.submit(inv, by=va, data={**good, **bad})
        assert field in e.value.message_dict
    services.submit(inv, by=va, data=good)
    dup = services.create_from_upload(vendor, by=va, uploaded=fake_pdf())
    with pytest.raises(ValidationError) as e:
        services.submit(dup, by=va, data={**good, "invoice_no": "x-1"})
    assert "invoice_no" in e.value.message_dict


def test_upload_rejects_wrong_type_and_size(seed, users, settings):
    from django.core.files.uploadedfile import SimpleUploadedFile

    with pytest.raises(ValidationError):
        services.create_from_upload(seed["vendor_a"], by=users["vendora"], uploaded=SimpleUploadedFile("x.pdf", b"MZ\x90\x00 not a pdf"))
    settings.MAX_UPLOAD_BYTES = 10
    with pytest.raises(ValidationError):
        services.create_from_upload(seed["vendor_a"], by=users["vendora"], uploaded=fake_pdf())


def test_maker_checker(make_invoice, users):
    from apps.accounts.models import Role, UserRole

    inv = make_invoice(total=1500000)
    with pytest.raises(PermissionDenied):  # uploader is a vendor user, has no approval role either
        services.act(inv, by=users["vendora"], action="APPROVE")
    with pytest.raises(PermissionDenied):  # L1 cannot act at finance step
        services.act(inv, by=users["l1"], action="APPROVE")
    services.act(inv, by=users["finance"], action="APPROVE", fin_data=FIN)
    # Finance user who also holds L1 and L2 cannot approve the invoice they pre-checked
    for code in ("L1", "L2"):
        UserRole.objects.create(user=users["finance"], role=Role.objects.get(code=code))
    fin = type(users["finance"]).objects.get(pk=users["finance"].pk)
    inv.refresh_from_db()
    with pytest.raises(PermissionDenied):
        services.act(inv, by=fin, action="APPROVE")
    # L1 approver also holding L2 cannot act again at L2
    UserRole.objects.create(user=users["l1"], role=Role.objects.get(code="L2"))
    services.act(inv, by=users["l1"], action="APPROVE")
    inv.refresh_from_db()
    l1 = type(users["l1"]).objects.get(pk=users["l1"].pk)
    with pytest.raises(PermissionDenied):
        services.act(inv, by=l1, action="APPROVE")
    assert inv not in services.queue_for(l1)


def test_query_returns_to_same_level(make_invoice, users):
    inv = make_invoice(total=1500000)
    services.act(inv, by=users["finance"], action="APPROVE", fin_data=FIN)
    services.act(inv, by=users["l1"], action="APPROVE")
    with pytest.raises(ValidationError):
        services.act(inv, by=users["l2"], action="QUERY", comment="")
    mail.outbox.clear()
    services.act(inv, by=users["l2"], action="QUERY", comment="Please attach the timesheet")
    inv.refresh_from_db()
    assert inv.status == S.QUERY_RAISED and inv.query_level == "L2"
    q = [m for m in mail.outbox if "Query" in m.subject][0]
    assert "vendora@test.local" in q.to and "l2@test.local" in q.cc
    services.vendor_reply(inv, by=users["vendora"], body="Attached", uploaded=fake_pdf("timesheet.pdf"))
    inv.refresh_from_db()
    assert inv.status == S.PENDING_L2 and inv.current_level == "L2"
    assert any("replied" in m.subject and m.to == ["l2@test.local"] for m in mail.outbox)


def test_reject_is_final(make_invoice, users):
    inv = make_invoice(total=50000)
    services.act(inv, by=users["finance"], action="REJECT", comment="Wrong GST rate")
    inv.refresh_from_db()
    assert inv.status == S.REJECTED
    assert any("rejected" in m.subject for m in mail.outbox)
    with pytest.raises(PermissionDenied):
        services.act(inv, by=users["finance"], action="APPROVE", fin_data=FIN)
    make_invoice(total=50000, invoice_no=inv.invoice_no)  # same number can be re-submitted after reject


def test_matrix_change_only_affects_new_invoices(make_invoice, users):
    old = make_invoice(total=200000, invoice_no="OLD-1")
    assert old.required_level == "L2"
    ApprovalMatrix.objects.filter(category="", cost_centre__isnull=True).update(l1_max=Decimal("500000"))
    new = make_invoice(total=200000, invoice_no="NEW-1")
    assert new.required_level == "L1"
    old.refresh_from_db()
    assert old.required_level == "L2"


def test_cost_centre_specific_matrix_wins(make_invoice):
    cc = CostCentre.objects.get(code="CC-OPS")
    ApprovalMatrix.objects.create(cost_centre=cc, l1_max=Decimal("10000"), l2_max=Decimal("20000"), requires_l3_above=Decimal("20000"))
    assert make_invoice(total=50000).required_level == "L3"


def test_payments_partial_then_full(make_invoice, users):
    inv = approve_chain(make_invoice(total=50000), users, ["L1"])
    services.record_payment(inv, by=users["finance"], amount=Decimal("20000"), paid_on=date.today(), utr="UTR001", tds_deducted=Decimal("1000"))
    inv.refresh_from_db()
    assert inv.status == S.PARTLY_PAID and inv.outstanding == Decimal("29000")
    with pytest.raises(ValidationError):
        services.record_payment(inv, by=users["finance"], amount=Decimal("30000"), paid_on=date.today(), utr="UTR002")
    services.record_payment(inv, by=users["finance"], amount=Decimal("29000"), paid_on=date.today(), utr="UTR002")
    inv.refresh_from_db()
    assert inv.status == S.PAID
    advice = [m for m in mail.outbox if "Payment advice" in m.subject]
    assert len(advice) == 2 and "UTR002" in advice[-1].body and "vendora@test.local" in advice[-1].to


def test_bank_change_holds_payment(make_invoice, users, seed):
    inv = approve_chain(make_invoice(total=50000), users, ["L1"])
    acct = vendor_services.request_bank_change(seed["vendor_a"], by=users["vendora"], holder_name="Acme", account_no="111122223333", ifsc="ICIC0000001", bank_name="ICICI")
    with pytest.raises(ValidationError, match="on hold"):
        services.record_payment(inv, by=users["finance"], amount=Decimal("50000"), paid_on=date.today(), utr="U1")
    vendor_services.decide_bank_change(acct, by=users["finance"], approve=True)
    services.record_payment(inv, by=users["finance"], amount=Decimal("50000"), paid_on=date.today(), utr="U1")


def test_vendor_manager_and_admin_cannot_approve_or_pay(make_invoice, users):
    inv = make_invoice(total=50000)
    for u in ("vm", "admin", "auditor"):
        with pytest.raises(PermissionDenied):
            services.act(inv, by=users[u], action="APPROVE", fin_data=FIN)


def test_every_transition_is_audited(make_invoice, users):
    from apps.audit.models import AuditLog

    inv = approve_chain(make_invoice(total=50000), users, ["L1"])
    actions = list(AuditLog.objects.filter(entity="invoice", entity_id=str(inv.pk)).values_list("action", "actor__email"))
    assert ("invoice.submit", "vendora@test.local") in actions
    assert ("invoice.approve", "finance@test.local") in actions
    assert ("invoice.approve", "l1@test.local") in actions


def test_blocked_vendor_cannot_upload(seed, users, run_on_commit):
    vendor_services.block(seed["vendor_a"], by=users["vm"], reason="x")
    with pytest.raises(ValidationError):
        services.create_from_upload(seed["vendor_a"], by=users["vendora"], uploaded=fake_pdf())
