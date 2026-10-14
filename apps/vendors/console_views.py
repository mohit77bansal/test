"""Internal console vendor pages (/console/vendors/...)."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.permissions import has_perm, require_perm
from apps.core.views_utils import apply_errors, internal_required
from apps.reports.exports import VENDOR_COLUMNS, maybe_export

from . import services
from .forms import InviteVendorForm
from .models import Vendor, VendorBankAccount, VendorDocument, VendorGSTIN


@internal_required
@require_perm("vendor.view_all")
def vendor_list(request):
    qs = Vendor.objects.select_related("owner_manager").all()
    status, q = request.GET.get("status"), request.GET.get("q", "").strip()
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(legal_name__icontains=q) | qs.filter(vendor_code__icontains=q)
    export = maybe_export(request, list(qs), VENDOR_COLUMNS, "vendor-master")
    if export:
        return export
    return render(request, "console/vendors.html", {"vendors": qs, "statuses": Vendor.STATUSES, "status": status, "q": q})


@internal_required
@require_perm("vendor.invite")
def vendor_invite(request):
    form = InviteVendorForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            vendor, _ = services.invite_vendor(by=request.user, **form.cleaned_data)
            messages.success(request, f"Invitation sent to {vendor.contact_email} ({vendor.vendor_code}).")
            return redirect("console:vendor_detail", pk=vendor.pk)
        except ValidationError as e:
            apply_errors(form, e)
    return render(request, "console/vendor_invite.html", {"form": form})


@internal_required
@require_perm("vendor.view_all")
def vendor_detail(request, pk):
    vendor = get_object_or_404(Vendor, pk=pk)
    docs = services.current_documents(vendor)
    from .models import DocumentType

    rows = [{"type": dt, "doc": docs.get(dt.pk)} for dt in DocumentType.for_category(vendor.category)]
    is_inviter = vendor.invited_by_id == request.user.pk
    return render(request, "console/vendor_detail.html", {
        "vendor": vendor, "rows": rows, "mismatches": services.mismatches(vendor) if vendor.pan_enc else [],
        "gstins": vendor.gstins.all(), "accounts": vendor.bank_accounts.order_by("-version", "-created_at"),
        "users": vendor.vendor_users.select_related("user"), "is_inviter": is_inviter,
        "can_review": has_perm(request.user, "vendor.review_kyc") and not is_inviter and vendor.status in (Vendor.SUBMITTED, Vendor.UNDER_REVIEW),
        "show_full": has_perm(request.user, "bank.approve"),
    })


def _do(request, fn, success, **kwargs):
    try:
        fn(by=request.user, **kwargs)
        messages.success(request, success)
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, " ".join(getattr(e, "messages", None) or [str(e)]))


@internal_required
@require_perm("vendor.review_kyc")
@require_POST
def review_document(request, pk, doc_pk):
    doc = get_object_or_404(VendorDocument, pk=doc_pk, vendor_id=pk)
    approve = request.POST.get("decision") == "approve"
    _do(request, lambda **kw: services.review_document(doc, **kw), f"{doc.document_type.name} {'approved' if approve else 'rejected'}.", approve=approve, remarks=request.POST.get("remarks", ""))
    return redirect("console:vendor_detail", pk=pk)


@internal_required
@require_POST
def vendor_action(request, pk, action):
    vendor = get_object_or_404(Vendor, pk=pk)
    remarks = request.POST.get("remarks", "")
    actions = {
        "send-back": ("vendor.review_kyc", lambda **kw: services.send_back(vendor, remarks=remarks, **kw), "Sent back to the vendor."),
        "activate": ("vendor.activate", lambda **kw: services.activate(vendor, **kw), "Vendor activated."),
        "reject": ("vendor.activate", lambda **kw: services.reject_vendor(vendor, remarks=remarks, **kw), "Vendor rejected."),
        "block": ("vendor.block", lambda **kw: services.block(vendor, reason=remarks, **kw), "Vendor blocked."),
        "unblock": ("vendor.block", lambda **kw: services.unblock(vendor, **kw), "Vendor unblocked."),
    }
    if action not in actions:
        raise PermissionDenied
    perm, fn, msg = actions[action]
    if not has_perm(request.user, perm):
        raise PermissionDenied
    _do(request, fn, msg)
    return redirect("console:vendor_detail", pk=pk)


@internal_required
@require_perm("vendor.verify_gstin")
@require_POST
def verify_gstin(request, pk, gstin_pk):
    g = get_object_or_404(VendorGSTIN, pk=gstin_pk, vendor_id=pk)
    _do(request, lambda **kw: services.verify_gstin(g, **kw), f"GSTIN {g.gstin} verified.")
    return redirect("console:vendor_detail", pk=pk)


@internal_required
@require_perm("bank.approve")
def bank_changes(request):
    pending = VendorBankAccount.objects.select_related("vendor", "requested_by").filter(status=VendorBankAccount.PENDING, vendor__status=Vendor.ACTIVE)
    rows = [{"acct": a, "current": a.vendor.active_bank_account} for a in pending]
    return render(request, "console/bank_changes.html", {"rows": rows})


@internal_required
@require_perm("bank.approve")
@require_POST
def bank_decide(request, pk):
    acct = get_object_or_404(VendorBankAccount, pk=pk)
    approve = request.POST.get("decision") == "approve"
    _do(request, lambda **kw: services.decide_bank_change(acct, **kw), "Bank change " + ("approved." if approve else "rejected."), approve=approve, remarks=request.POST.get("remarks", ""))
    return redirect("console:bank_changes")
