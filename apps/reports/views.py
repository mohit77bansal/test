from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.http import Http404
from django.shortcuts import render

from apps.core.permissions import require_perm
from apps.core.views_utils import internal_required
from apps.invoices.models import Invoice, Payment
from apps.invoices.services import LEVEL_LABEL, level_pool
from apps.vendors.models import Vendor

from .exports import INVOICE_COLUMNS, PAYMENT_COLUMNS, VENDOR_COLUMNS, maybe_export

S = Invoice.Status
OPEN = [S.FIN_PRECHECK, S.PENDING_L1, S.PENDING_L2, S.PENDING_L3, S.QUERY_RAISED, S.APPROVED, S.PARTLY_PAID]
BUCKETS = [("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, 10**6)]


def bucket_for(days):
    for label, lo, hi in BUCKETS:
        if lo <= days <= hi:
            return label
    return BUCKETS[0][0]


def ageing_rows():
    today = date.today()
    rows = []
    for inv in Invoice.objects.select_related("vendor", "gstin", "cost_centre").filter(status__in=OPEN):
        days = (today - (inv.invoice_date or today)).days
        rows.append({"invoice": inv, "days": days, "bucket": bucket_for(days), "outstanding": inv.outstanding})
    return rows


def pending_by_approver_rows():
    agg = defaultdict(lambda: {"count": 0, "amount": Decimal("0"), "oldest": None})
    for inv in Invoice.objects.select_related("vendor", "cost_centre").filter(status__in=[S.FIN_PRECHECK, S.PENDING_L1, S.PENDING_L2, S.PENDING_L3]):
        for user in level_pool(inv, inv.current_level):
            if user.pk == inv.submitted_by_id:
                continue
            key = (user.name, user.email, LEVEL_LABEL[inv.current_level])
            a = agg[key]
            a["count"] += 1
            a["amount"] += inv.total or 0
            if a["oldest"] is None or (inv.submitted_at and inv.submitted_at < a["oldest"]):
                a["oldest"] = inv.submitted_at
    return [{"approver": k[0], "email": k[1], "level": k[2], **v} for k, v in sorted(agg.items())]


REPORTS = {
    "vendor-master": "Vendor master",
    "ageing": "Invoice ageing",
    "pending-by-approver": "Pending approvals by approver",
    "payments": "Payments register",
}


@internal_required
@require_perm("report.view")
def report(request, name):
    if name not in REPORTS:
        raise Http404
    ctx = {"name": name, "title": REPORTS[name], "reports": REPORTS}
    if name == "vendor-master":
        items = list(Vendor.objects.exclude(status=Vendor.INVITED))
        export = maybe_export(request, items, VENDOR_COLUMNS, name)
        ctx["vendors"] = items
    elif name == "ageing":
        rows = ageing_rows()
        cols = [("Bucket", lambda r: r["bucket"]), ("Days", lambda r: r["days"])] + [(label, (lambda g: lambda r: g(r["invoice"]))(getter)) for label, getter in INVOICE_COLUMNS]
        export = maybe_export(request, rows, cols, name)
        summary = {b[0]: {"count": 0, "amount": Decimal("0")} for b in BUCKETS}
        for r in rows:
            summary[r["bucket"]]["count"] += 1
            summary[r["bucket"]]["amount"] += r["outstanding"]
        ctx.update(rows=rows, summary=summary)
    elif name == "pending-by-approver":
        rows = pending_by_approver_rows()
        cols = [("Approver", lambda r: r["approver"]), ("Email", lambda r: r["email"]), ("Level", lambda r: r["level"]), ("Pending count", lambda r: r["count"]), ("Pending amount", lambda r: r["amount"]), ("Oldest since", lambda r: r["oldest"])]
        export = maybe_export(request, rows, cols, name)
        ctx["rows"] = rows
    else:
        items = list(Payment.objects.select_related("invoice", "vendor"))
        export = maybe_export(request, items, PAYMENT_COLUMNS, name)
        ctx["payments"] = items
    return export or render(request, "console/report.html", ctx)
