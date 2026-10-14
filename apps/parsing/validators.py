"""Indian tax identifier and invoice rule checks."""
import re
from datetime import date
from decimal import Decimal

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
UDYAM_RE = re.compile(r"^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$")
_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = _CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _CHARS[(36 - total % 36) % 36]


def valid_gstin(gstin: str) -> bool:
    gstin = (gstin or "").strip().upper()
    return bool(GSTIN_RE.match(gstin)) and gstin_check_char(gstin[:14]) == gstin[14]


def valid_pan(pan: str) -> bool:
    return bool(PAN_RE.match((pan or "").strip().upper()))


def valid_ifsc(ifsc: str) -> bool:
    return bool(IFSC_RE.match((ifsc or "").strip().upper()))


def pan_from_gstin(gstin: str) -> str:
    return (gstin or "")[2:12].upper()


def make_gstin(state_code: str, pan: str, entity: str = "1") -> str:
    first14 = f"{state_code}{pan}{entity}Z"
    return first14 + gstin_check_char(first14)


def financial_year(d: date) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def totals_reconcile(taxable, cgst, sgst, igst, total, tolerance=Decimal("1.00")) -> bool:
    parts = [Decimal(str(x or 0)) for x in (taxable, cgst, sgst, igst)]
    return abs(sum(parts) - Decimal(str(total or 0))) <= Decimal(str(tolerance))
