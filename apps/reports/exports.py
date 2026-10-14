"""CSV / Excel export of any list view: `?export=csv` or `?export=xlsx`.

Column order is part of the contract with Finance (kept stable for a later accounting integration).
"""
import csv
import io
from datetime import date, datetime
from decimal import Decimal

from django.http import HttpResponse
from openpyxl import Workbook


def _cell(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if value is None:
        return ""
    if isinstance(value, (date, int, float)):
        return value
    return str(value)


def rows_for(items, columns):
    for item in items:
        yield [_cell(getter(item)) for _, getter in columns]


def export_response(items, columns, filename, fmt):
    headers = [label for label, _ in columns]
    if fmt == "xlsx":
        wb = Workbook(write_only=True)
        ws = wb.create_sheet(filename[:31])
        ws.append(headers)
        for row in rows_for(items, columns):
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
        return resp
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows_for(items, columns):
        writer.writerow([v.isoformat() if isinstance(v, (date, datetime)) else v for v in row])
    resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    return resp


def maybe_export(request, items, columns, filename):
    fmt = request.GET.get("export")
    if fmt in ("csv", "xlsx"):
        from apps.audit import services as audit
        from apps.core.permissions import has_perm

        if request.user.is_internal and not has_perm(request.user, "report.export"):
            return None
        audit.log("export." + fmt, "export", filename, after={"rows": len(items) if hasattr(items, "__len__") else None, "query": request.GET.urlencode()})
        return export_response(items, columns, filename, fmt)
    return None


# Shared column sets ---------------------------------------------------------------------

INVOICE_COLUMNS = [
    ("Portal ref", lambda i: i.portal_ref),
    ("Vendor code", lambda i: i.vendor.vendor_code),
    ("Vendor", lambda i: i.vendor.legal_name),
    ("Supplier GSTIN", lambda i: i.gstin.gstin if i.gstin_id else ""),
    ("Invoice no", lambda i: i.invoice_no),
    ("Invoice date", lambda i: i.invoice_date),
    ("FY", lambda i: i.fy),
    ("Cost centre", lambda i: i.cost_centre.code if i.cost_centre_id else ""),
    ("Taxable", lambda i: i.taxable),
    ("CGST", lambda i: i.cgst),
    ("SGST", lambda i: i.sgst),
    ("IGST", lambda i: i.igst),
    ("Total", lambda i: i.total),
    ("TDS section", lambda i: i.tds_section),
    ("TDS amount", lambda i: i.tds_amount),
    ("Status", lambda i: i.status),
    ("Due date", lambda i: i.due_date),
    ("Paid", lambda i: i.paid_amount),
    ("Outstanding", lambda i: i.outstanding),
]

PAYMENT_COLUMNS = [
    ("Paid on", lambda p: p.paid_on),
    ("Portal ref", lambda p: p.invoice.portal_ref),
    ("Vendor code", lambda p: p.vendor.vendor_code),
    ("Vendor", lambda p: p.vendor.legal_name),
    ("Invoice no", lambda p: p.invoice.invoice_no),
    ("Amount", lambda p: p.amount),
    ("TDS deducted", lambda p: p.tds_deducted),
    ("UTR", lambda p: p.utr),
]

VENDOR_COLUMNS = [
    ("Vendor code", lambda v: v.vendor_code),
    ("Legal name", lambda v: v.legal_name),
    ("Trade name", lambda v: v.trade_name),
    ("Category", lambda v: v.category),
    ("PAN (last 4)", lambda v: v.pan_last4),
    ("Primary GSTIN", lambda v: v.primary_gstin.gstin if v.primary_gstin else ""),
    ("MSME status", lambda v: v.msme_status),
    ("Udyam no", lambda v: v.udyam_no),
    ("Contact", lambda v: v.contact_name),
    ("Email", lambda v: v.contact_email),
    ("Status", lambda v: v.status),
    ("Activated", lambda v: v.activated_at),
]
