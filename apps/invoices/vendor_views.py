"""Vendor portal invoice pages (/vendor/invoices/...)."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.permissions import require_perm
from apps.core.views_utils import apply_errors, vendor_required
from apps.reports.exports import INVOICE_COLUMNS, PAYMENT_COLUMNS, maybe_export

from . import services
from .forms import InvoiceForm, ReplyForm, UploadForm
from .models import Invoice, Payment

S = Invoice.Status


def _own(request, pk):
    return get_object_or_404(Invoice.objects.for_user(request.user).select_related("vendor", "parse_job", "file", "cost_centre"), pk=pk)


@vendor_required
def invoice_list(request):
    qs = Invoice.objects.for_user(request.user).select_related("vendor", "gstin", "cost_centre").exclude(status=S.DRAFT)
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    export = maybe_export(request, list(qs), INVOICE_COLUMNS, "invoices")
    if export:
        return export
    drafts = Invoice.objects.for_user(request.user).filter(status=S.DRAFT).order_by("-created_at")[:10]
    return render(request, "vendor/invoices.html", {"invoices": qs, "drafts": drafts, "statuses": S.choices, "status": status})


@vendor_required(active=True)
@require_perm("invoice.upload")
def upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            inv = services.create_from_upload(request.vendor, by=request.user, uploaded=form.cleaned_data["file"])
            return redirect("vendor:invoice_edit", pk=inv.pk)
        except ValidationError as e:
            apply_errors(form, e)
    return render(request, "vendor/invoice_upload.html", {"form": form})


def _edit_context(request, inv, form=None):
    initial, low, line_items = services.parsed_initial(inv)
    if inv.cost_centre_id:
        initial["cost_centre"] = inv.cost_centre_id
    if form is None:
        form = InvoiceForm(initial=initial)
    job = inv.parse_job
    return {
        "invoice": inv, "form": form, "job": job, "low": low, "line_items": line_items,
        "reading": job is not None and not job.finished, "checks": [c for c in (job.checks if job else []) if not c["ok"]],
    }


@vendor_required(active=True)
@require_perm("invoice.upload")
def edit(request, pk):
    inv = _own(request, pk)
    if inv.status != S.DRAFT:
        return redirect("vendor:invoice_detail", pk=inv.pk)
    if request.method == "POST":
        form = InvoiceForm(request.POST)
        if form.is_valid():
            _, _, line_items = services.parsed_initial(inv)
            try:
                inv = services.submit(inv, by=request.user, data=form.cleaned_data, line_items=line_items)
                messages.success(request, f"Invoice submitted. Portal reference {inv.portal_ref}.")
                return redirect("vendor:invoice_detail", pk=inv.pk)
            except ValidationError as e:
                apply_errors(form, e)
        return render(request, "vendor/invoice_edit.html", _edit_context(request, inv, form))
    return render(request, "vendor/invoice_edit.html", _edit_context(request, inv))


@vendor_required
def parse_status(request, pk):
    """HTMX poll target (every 2s) while the parser runs."""
    inv = _own(request, pk)
    ctx = _edit_context(request, inv)
    return render(request, "partials/invoice_parse.html", ctx)


@vendor_required
@require_POST
def discard(request, pk):
    inv = _own(request, pk)
    if inv.status != S.DRAFT:
        raise Http404
    inv.delete()
    messages.info(request, "Draft discarded.")
    return redirect("vendor:invoices")


@vendor_required
def detail(request, pk):
    inv = _own(request, pk)
    if inv.status == S.DRAFT:
        return redirect("vendor:invoice_edit", pk=inv.pk)
    form = ReplyForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if form.is_valid():
            try:
                services.vendor_reply(inv, by=request.user, body=form.cleaned_data["body"], uploaded=form.cleaned_data.get("file"))
                messages.success(request, "Reply sent.")
                return redirect("vendor:invoice_detail", pk=inv.pk)
            except ValidationError as e:
                apply_errors(form, e)
    return render(request, "vendor/invoice_detail.html", {
        "invoice": inv, "form": form, "messages_thread": inv.messages.select_related("from_user", "file"),
        "payments": inv.payments.all(), "actions": inv.actions.select_related("actor"),
    })


@vendor_required
def payments(request):
    qs = Payment.objects.for_user(request.user).select_related("invoice", "vendor")
    export = maybe_export(request, list(qs), PAYMENT_COLUMNS, "payments")
    if export:
        return export
    outstanding = Invoice.objects.for_user(request.user).filter(status__in=[S.APPROVED, S.PARTLY_PAID])
    return render(request, "vendor/payments.html", {"payments": qs, "outstanding": outstanding})
