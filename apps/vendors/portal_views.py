"""Vendor portal (/vendor/) — onboarding, profile, users, bank details."""
from django.contrib import messages
from django.contrib.auth import login
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.forms import SetPasswordForm
from apps.core.permissions import require_perm
from apps.core.rls import db_context, internal_context
from apps.core.views_utils import apply_errors, vendor_required
from apps.invoices.models import Invoice
from apps.parsing.models import File
from apps.parsing.services import file_response

from . import services
from .forms import BankChangeForm, DocumentUploadForm, GstinForm, RegistrationForm, VendorUserForm
from .models import DocumentType, Vendor, VendorBankAccount, VendorUser


def accept_invite(request, token):
    with internal_context():
        invite = services.find_invite(token)
    if invite is None or not invite.is_valid:
        return render(request, "vendor/invite_invalid.html", status=404)
    form = SetPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with db_context(internal=False, vendor_id=invite.vendor_id):
            try:
                user = services.accept_invite(invite, password=form.cleaned_data["password1"])
            except ValidationError as e:
                apply_errors(form, e)
            else:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                messages.success(request, "Welcome! Your password is set.")
                return redirect("vendor:home")
    return render(request, "vendor/accept_invite.html", {"form": form, "invite": invite})


@vendor_required
def home(request):
    vendor = request.vendor
    if vendor.status != Vendor.ACTIVE:
        return redirect("vendor:onboarding")
    invoices = Invoice.objects.for_user(request.user).exclude(status=Invoice.Status.DRAFT)
    counts = {
        "open": invoices.filter(status__in=["FIN_PRECHECK", "PENDING_L1", "PENDING_L2", "PENDING_L3"]).count(),
        "query": invoices.filter(status="QUERY_RAISED").count(),
        "approved": invoices.filter(status__in=["APPROVED", "PARTLY_PAID"]).count(),
        "paid": invoices.filter(status="PAID").count(),
    }
    return render(request, "vendor/dashboard.html", {"vendor": vendor, "counts": counts, "recent": invoices[:8]})


@vendor_required
def onboarding(request):
    vendor = request.vendor
    can_edit = vendor.status == Vendor.DRAFT and request.user.has_role("VENDOR_ADMIN")
    acct = vendor.bank_accounts.exclude(status__in=[VendorBankAccount.REJECTED, VendorBankAccount.SUPERSEDED]).order_by("-created_at").first()
    initial = {
        "legal_name": vendor.legal_name, "trade_name": vendor.trade_name, "constitution": vendor.constitution,
        "pan": vendor.pan, "gstin": vendor.primary_gstin.gstin if vendor.primary_gstin else "", "address": vendor.address,
        "contact_name": vendor.contact_name, "contact_phone": vendor.contact_phone, "msme_status": vendor.msme_status,
        "udyam_no": vendor.udyam_no, "msme_type": vendor.msme_type, "consent": bool(vendor.consent_at),
        "holder_name": acct.holder_name if acct else "", "ifsc": acct.ifsc if acct else "", "bank_name": acct.bank_name if acct else "",
    }
    parsed = services.parsed_values(vendor)
    prefilled = set()
    for k, v in parsed.items():
        if not initial.get(k) and k != "account_no":
            initial[k] = v
            prefilled.add(k)
    if not acct and parsed.get("account_no"):
        initial["account_no"] = parsed["account_no"]
        prefilled.add("account_no")
    form = RegistrationForm(request.POST or None, initial=initial, account_required=acct is None)
    if request.method == "POST":
        if not can_edit:
            raise Http404
        if form.is_valid():
            try:
                services.save_registration(vendor, by=request.user, data=form.cleaned_data)
                messages.success(request, "Registration saved.")
                return redirect("vendor:documents")
            except ValidationError as e:
                apply_errors(form, e)
    return render(request, "vendor/onboarding.html", {
        "vendor": vendor, "form": form, "can_edit": can_edit, "account": acct, "prefilled": prefilled,
        "mismatches": services.mismatches(vendor) if vendor.pan_enc else [], "step": "profile",
    })


@vendor_required
def documents(request):
    vendor = request.vendor
    can_edit = vendor.status == Vendor.DRAFT and request.user.has_role("VENDOR_ADMIN")
    docs = services.current_documents(vendor)
    rows = [{"type": dt, "doc": docs.get(dt.pk), "form": DocumentUploadForm(prefix=dt.code)} for dt in DocumentType.for_category(vendor.category)]
    return render(request, "vendor/documents.html", {
        "vendor": vendor, "rows": rows, "can_edit": can_edit, "mismatches": services.mismatches(vendor) if vendor.pan_enc else [],
        "step": "documents", "pending_parse": any(r["doc"] and r["doc"].parse_job and not r["doc"].parse_job.finished for r in rows),
    })


@vendor_required
@require_perm("vendor.edit_profile")
@require_POST
def upload_document(request, code):
    vendor = request.vendor
    dt = get_object_or_404(DocumentType.for_category(vendor.category), code=code)
    form = DocumentUploadForm(request.POST, request.FILES, prefix=dt.code)
    if form.is_valid():
        try:
            services.upload_document(vendor, by=request.user, document_type=dt, uploaded=form.cleaned_data["file"], valid_from=form.cleaned_data.get("valid_from"), valid_to=form.cleaned_data.get("valid_to"))
            messages.success(request, f"{dt.name} uploaded.")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    else:
        messages.error(request, f"{dt.name}: " + " ".join(m for errs in form.errors.values() for m in errs))
    return redirect("vendor:documents")


@vendor_required
def documents_status(request):
    """HTMX: re-render the document table while parse jobs are running."""
    response = documents(request)
    return response


@vendor_required
@require_perm("vendor.edit_profile")
@require_POST
def submit_registration(request):
    try:
        services.submit_registration(request.vendor, by=request.user)
        messages.success(request, "Submitted for review. We will email you when the Vendor Manager has reviewed it.")
    except ValidationError as e:
        for m in e.messages:
            messages.error(request, m)
    return redirect("vendor:documents")


@vendor_required
def file_download(request, pk):
    f = get_object_or_404(File.objects.for_user(request.user), pk=pk)
    if not f.is_viewable:
        raise Http404
    return file_response(f)


@vendor_required
def users(request):
    vendor = request.vendor
    is_admin = request.user.has_role("VENDOR_ADMIN")
    form = VendorUserForm(request.POST or None)
    if request.method == "POST":
        if not is_admin:
            raise Http404
        if form.is_valid():
            try:
                services.add_vendor_user(vendor, by=request.user, **form.cleaned_data)
                messages.success(request, f"Invitation sent to {form.cleaned_data['email']}.")
                return redirect("vendor:users")
            except ValidationError as e:
                apply_errors(form, e)
    members = VendorUser.objects.for_user(request.user).select_related("user").filter(is_active=True)
    return render(request, "vendor/users.html", {"vendor": vendor, "members": members, "form": form, "is_admin": is_admin})


@vendor_required
@require_perm("vendor.manage_users")
@require_POST
def remove_user(request, pk):
    vu = get_object_or_404(VendorUser.objects.for_user(request.user), pk=pk, is_active=True)
    try:
        services.remove_vendor_user(vu, by=request.user)
        messages.success(request, f"{vu.user.email} removed.")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect("vendor:users")


@vendor_required
def profile(request):
    vendor = request.vendor
    gform = GstinForm(request.POST or None)
    if request.method == "POST":
        if not request.user.has_role("VENDOR_ADMIN"):
            raise Http404
        if gform.is_valid():
            try:
                services.add_gstin(vendor, by=request.user, **gform.cleaned_data)
                messages.success(request, "GSTIN added. It can be used on invoices once the Vendor Manager verifies it.")
                return redirect("vendor:profile")
            except ValidationError as e:
                apply_errors(gform, e)
    return render(request, "vendor/profile.html", {"vendor": vendor, "gform": gform, "gstins": vendor.gstins.all(), "account": vendor.active_bank_account})


@vendor_required(active=True)
def bank(request):
    vendor = request.vendor
    is_admin = request.user.has_role("VENDOR_ADMIN")
    form = BankChangeForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if not is_admin:
            raise Http404
        if form.is_valid():
            try:
                data = form.cleaned_data
                services.request_bank_change(vendor, by=request.user, holder_name=data["holder_name"], account_no=data["account_no"], ifsc=data["ifsc"], bank_name=data["bank_name"], uploaded=data.get("cheque"))
                messages.success(request, "Bank details change submitted. Payments are on hold until Finance verifies it.")
                return redirect("vendor:bank")
            except ValidationError as e:
                apply_errors(form, e)
    history = VendorBankAccount.objects.for_user(request.user).order_by("-version", "-created_at")
    return render(request, "vendor/bank.html", {"vendor": vendor, "form": form, "history": history, "is_admin": is_admin, "pending": vendor.has_pending_bank_change})
