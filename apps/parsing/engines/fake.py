"""Deterministic parser for tests and local dev.

A file may embed `FAKE_PARSE:{json}:END` to override fields (and `confidence`). Otherwise
sensible fixtures are returned, with `invoice_date` deliberately low-confidence so the amber
highlight is always exercised.
"""
import json
import re
from datetime import date

from apps.parsing.validators import make_gstin

MARKER = re.compile(rb"FAKE_PARSE:(\{.*?\}):END", re.S)


def _override(content: bytes):
    m = MARKER.search(content or b"")
    if not m:
        return {}
    try:
        return json.loads(m.group(1).decode())
    except ValueError:
        return {}


class FakeEngine:
    name = "fake"

    def parse(self, kind, content: bytes, *, vendor=None, sha256=""):
        gstin = ""
        pan = ""
        if vendor is not None:
            primary = vendor.gstins.filter(is_primary=True).first()
            gstin = primary.gstin if primary else ""
            pan = vendor.pan
        gstin = gstin or make_gstin("27", "AAACA1234A")
        pan = pan or gstin[2:12]
        legal = vendor.legal_name if vendor is not None else "Acme Supplies Private Limited"
        defaults = {
            "INVOICE": {
                "supplier_gstin": gstin,
                "invoice_no": f"INV-{sha256[:6].upper() or '000001'}",
                "invoice_date": date.today().isoformat(),
                "po_no": "",
                "line_items": [
                    {"description": "Professional services", "hsn_sac": "998313", "qty": "1", "rate": "42372.88", "amount": "42372.88", "tax_rate": "18"}
                ],
                "taxable": "42372.88",
                "cgst": "3813.56",
                "sgst": "3813.56",
                "igst": "0",
                "total": "50000.00",
            },
            "PAN": {"pan": pan, "name": legal},
            "GST": {"gstin": gstin, "legal_name": legal, "trade_name": legal, "address": "Mumbai, Maharashtra"},
            "CHEQUE": {"account_no": "123456789012", "ifsc": "HDFC0001234", "holder_name": legal, "bank_name": "HDFC Bank"},
            "UDYAM": {"udyam_no": "UDYAM-MH-19-0012345", "name": legal, "msme_type": "SMALL"},
        }[kind]
        override = _override(content)
        confidence = {k: 0.97 for k in defaults if k != "line_items"}
        if kind == "INVOICE":
            confidence["invoice_date"] = 0.62
        confidence.update(override.pop("confidence", {}))
        extracted = {**defaults, **override}
        return {"engine": "fake", "override": bool(override)}, extracted, confidence
