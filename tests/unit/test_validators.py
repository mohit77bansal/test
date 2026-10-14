from datetime import date
from decimal import Decimal

from apps.core.templatetags.portal_tags import inr
from apps.parsing.validators import financial_year, make_gstin, pan_from_gstin, totals_reconcile, valid_gstin, valid_ifsc, valid_pan


def test_gstin_checksum_round_trip():
    g = make_gstin("27", "AAACA1234A")
    assert valid_gstin(g)
    assert pan_from_gstin(g) == "AAACA1234A"
    bad = g[:-1] + ("0" if g[-1] != "0" else "1")
    assert not valid_gstin(bad)


def test_known_real_format_gstin():
    assert valid_gstin("27AAPFU0939F1ZV")


def test_pan_and_ifsc():
    assert valid_pan("AAACA1234A") and not valid_pan("AAAC1234A")
    assert valid_ifsc("HDFC0001234") and not valid_ifsc("HDFC1001234")


def test_financial_year():
    assert financial_year(date(2026, 3, 31)) == "2025-26"
    assert financial_year(date(2026, 4, 1)) == "2026-27"


def test_totals_tolerance():
    assert totals_reconcile("100", "9", "9", "0", "118")
    assert totals_reconcile("100", "9", "9", "0", "119.00")
    assert not totals_reconcile("100", "9", "9", "0", "119.01", tolerance=Decimal("1"))


def test_inr_grouping():
    assert inr(Decimal("1500000")) == "₹15,00,000.00"
    assert inr(50000) == "₹50,000.00"
    assert inr(999) == "₹999.00"
