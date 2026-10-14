"""Shared fixtures: every test starts from the seed state (docs/ARCHITECTURE.md §8)."""
import json
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile


@pytest.fixture(autouse=True)
def _media(request, tmp_path):
    if "settings" not in request.fixturenames and not request.config.pluginmanager.hasplugin("django"):
        return  # remote smoke runs with -p no:django
    request.getfixturevalue("settings").MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def seed(db):
    from apps.core.seed import seed_demo

    return seed_demo()


@pytest.fixture
def run_on_commit(monkeypatch):
    """Unit tests run inside one transaction; execute on_commit callbacks (Celery, emails) immediately."""
    from django.db import transaction

    monkeypatch.setattr(transaction, "on_commit", lambda fn, using=None, robust=False: fn())


@pytest.fixture
def users(seed):
    from apps.accounts.models import User

    return {u.email.split("@")[0]: u for u in User.objects.all()}


def fake_pdf(filename="invoice.pdf", **fields):
    """A tiny PDF whose fake-parser output is controlled by FAKE_PARSE fields."""
    marker = f"FAKE_PARSE:{json.dumps(fields)}:END".encode() if fields else b""
    return SimpleUploadedFile(filename, b"%PDF-1.4\n" + marker + b"\n%%EOF", content_type="application/pdf")


def invoice_fields(total, gstin, invoice_no="INV-1001", **extra):
    total = Decimal(str(total))
    taxable = (total / Decimal("1.18")).quantize(Decimal("0.01"))
    gst = total - taxable
    half = (gst / 2).quantize(Decimal("0.01"))
    return {
        "supplier_gstin": gstin, "invoice_no": invoice_no, "taxable": str(taxable), "cgst": str(half),
        "sgst": str(gst - half), "igst": "0", "total": str(total), **extra,
    }


@pytest.fixture
def make_invoice(seed, run_on_commit):
    """Upload + submit an invoice for a vendor via the services. Returns the submitted invoice."""
    from datetime import date

    from apps.core.models import CostCentre
    from apps.invoices import services

    def _make(vendor=None, total=50000, invoice_no=None, by=None):
        from apps.accounts.models import User

        vendor = vendor or seed["vendor_a"]
        by = by or User.objects.get(email="vendora@test.local" if vendor.vendor_code == "V00001" else "vendorb@test.local")
        invoice_no = invoice_no or f"INV-{total}-{vendor.vendor_code}"
        inv = services.create_from_upload(vendor, by=by, uploaded=fake_pdf())
        data = invoice_fields(total, vendor.primary_gstin.gstin, invoice_no)
        data = {k: (Decimal(v) if k in ("taxable", "cgst", "sgst", "igst", "total") else v) for k, v in data.items()}
        data.update(invoice_date=date.today(), cost_centre=CostCentre.objects.get(code="CC-OPS"))
        return services.submit(inv, by=by, data=data)

    return _make
