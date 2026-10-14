"""Internal console invoice pages: queues, approval actions, payments."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.permissions import has_perm, require_perm
from apps.core.views_utils import apply_errors, internal_required
from apps.parsing.models import File
from apps.parsing.services import file_response
from apps.reports.exports import INVOICE_COLUMNS, maybe_export
from apps.vendors.models import Vendor

from . import services
from .forms import ActionForm, PaymentForm
from .models import Invoice

S = Invoice.Status


@internal_required
def home(request):
    user = request.user
    queue = services.queue_for(user)
    ctx = {"queue": queue}
    if has_perm(user, "vendor.review_kyc"):
        ctx["kyc_queue"] = Vendor.objects.filter(status__in=[Vendor.SUBMITTED, Vendor.UNDER_REVIEW]).exclude(invited_by=user)
    if has_perm(user, "payment.record"):
        ctx["payment_count"] = Invoice.objects.filter(status__in=[S.APPROVED, S.PARTLY_PAID]).count()
    if has_perm(user, "bank.approve"):
        from apps.vendors.models import VendorBankAccount

        ctx["bank_count"] = VendorBankAccount.objects.filter(status="PENDING", vendor__status="ACTIVE").count()
    return render(request, "console/home.html", ctx)


@internal_required
@require_perm("invoice.view_all")
def invoice_list(request):
    qs = Invoice.objects.select_related("vendor", "gstin", "cost_centre").exclude(status=S.DRAFT)
    status, q = request.GET.get("status"), request.GET.get("q", "").strip()
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(portal_ref__icontains=q) | qs.filter(invoice_no__icontains=q) | qs.filter(vendor__legal_name__icontains=q)
    export = maybe_export(request, list(qs), INVOICE_COLUMNS, "invoices")
    if export:
        return export
    return render(request, "console/invoices.html", {"invoices": qs, "statuses": S.choices, "status": status, "q": q})


@internal_required
@require_perm("invoice.view_all")
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("vendor", "gstin", "cost_centre", "file", "parse_job").exclude(status=S.DRAFT), pk=pk)
    can, reason = services.can_act(inv, request.user)
    form = ActionForm(request.POST or None, initial={"tds_section": inv.tds_section, "tds_amount": inv.tds_amount, "cost_centre": inv.cost_centre_id})
    if request.method == "POST":
        if not can:
            messages.error(request, reason)
            return redirect("console:invoice_detail", pk=pk)
        if form.is_valid():
            data = form.cleaned_data
            try:
                services.act(inv, by=request.user, action=data["action"], comment=data["comment"], fin_data=data)
                messages.success(request, {"APPROVE": "Approved.", "REJECT": "Rejected.", "QUERY": "Query sent to the vendor."}[data["action"]])
                return redirect("console:home")
            except PermissionDenied as e:
                messages.error(request, str(e))
                return redirect("console:invoice_detail", pk=pk)
            except ValidationError as e:
                apply_errors(form, e)
    return render(request, "console/invoice_detail.html", {
        "invoice": inv, "form": form, "can_act": can, "reason": reason, "is_finance_step": inv.current_level == "FIN_PRE",
        "messages_thread": inv.messages.select_related("from_user", "file"), "actions": inv.actions.select_related("actor"),
        "payments": inv.payments.select_related("recorded_by"), "low": inv.parse_job.low_confidence_fields() if inv.parse_job else set(),
    })


@internal_required
@require_perm("payment.record")
def payment_queue(request):
    qs = Invoice.objects.select_related("vendor", "gstin", "cost_centre").filter(status__in=[S.APPROVED, S.PARTLY_PAID]).order_by("due_date")
    export = maybe_export(request, list(qs), INVOICE_COLUMNS, "approved-invoices")
    if export:
        return export
    return render(request, "console/payment_queue.html", {"invoices": qs})


@internal_required
@require_perm("payment.record")
def record_payment(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("vendor"), pk=pk, status__in=[S.APPROVED, S.PARTLY_PAID])
    form = PaymentForm(request.POST or None, initial={"amount": inv.outstanding - (inv.tds_amount if not inv.payments.exists() else 0), "tds_deducted": inv.tds_amount if not inv.payments.exists() else 0, "paid_on": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        try:
            d = form.cleaned_data
            services.record_payment(inv, by=request.user, amount=d["amount"], paid_on=d["paid_on"], utr=d["utr"], tds_deducted=d.get("tds_deducted") or 0)
            messages.success(request, "Payment recorded and payment advice sent.")
            return redirect("console:payment_queue")
        except ValidationError as e:
            apply_errors(form, e)
    return render(request, "console/payment_form.html", {"invoice": inv, "form": form, "on_hold": inv.vendor.has_pending_bank_change})


@internal_required
@require_perm("file.view")
def file_download(request, pk):
    f = get_object_or_404(File, pk=pk)
    if not f.is_viewable:
        raise Http404
    return file_response(f)


