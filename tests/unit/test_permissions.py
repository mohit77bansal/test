from pathlib import Path

from django.conf import settings

from apps.core import permissions as P


def test_vendor_roles_cannot_approve_or_pay():
    for code in ("invoice.fin_precheck", "invoice.approve_l1", "invoice.approve_l2", "invoice.approve_l3", "payment.record", "bank.approve"):
        assert not P.roles_for(code) & set(P.VENDOR_ROLES)


def test_segregation_of_duties():
    approve = {"invoice.approve_l1", "invoice.approve_l2", "invoice.approve_l3", "vendor.review_kyc", "vendor.activate"}
    for code in approve:
        assert P.ADMIN not in P.roles_for(code)
        assert P.AUDITOR not in P.roles_for(code)
    for code in ("invoice.approve_l1", "invoice.approve_l2", "invoice.approve_l3", "payment.record"):
        assert P.VENDOR_MANAGER not in P.roles_for(code)
    for code in ("invoice.approve_l1", "invoice.approve_l2", "invoice.approve_l3", "vendor.edit_profile"):
        assert P.FINANCE not in P.roles_for(code)


def test_permissions_doc_is_current():
    path = Path(settings.BASE_DIR) / "docs" / "PERMISSIONS.md"
    assert path.read_text() == P.matrix_markdown(), "run: python manage.py permission_matrix"
