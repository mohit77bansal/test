"""Vendor onboarding state machine (docs/ARCHITECTURE.md §5). Views never set status directly."""
import secrets
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User, UserRole
from apps.audit import services as audit
from apps.core.models import SystemSetting
from apps.core.permissions import FINANCE, VENDOR_ADMIN, VENDOR_MANAGER, VENDOR_USER, has_perm
from apps.core.sequences import next_value
from apps.notify.services import notify
from apps.parsing.services import start_parse, store_upload
from apps.parsing.validators import pan_from_gstin, valid_gstin, valid_ifsc, valid_pan

from .models import DocumentType, Vendor, VendorBankAccount, VendorDocument, VendorGSTIN, VendorInvite, VendorUser


def _require(user, code):
    if not has_perm(user, code):
        raise PermissionDenied(code)


def _set_status(vendor, new_status, action, **extra):
    before = {"status": vendor.status}
    vendor.status = new_status
    for k, v in extra.items():
        setattr(vendor, k, v)
    vendor.save()
    audit.log(action, "vendor", vendor.pk, before=before, after={"status": new_status, **{k: v for k, v in extra.items() if isinstance(v, str)}})


def internal_users_with_role(code):
    return User.objects.filter(user_type=User.INTERNAL, status=User.ACTIVE, user_roles__role__code=code).distinct()


def vendor_admins(vendor):
    return [vu.user for vu in vendor.vendor_users.select_related("user").filter(is_admin=True, is_active=True)]


def vendor_all_users(vendor):
    return [vu.user for vu in vendor.vendor_users.select_related("user").filter(is_active=True)]


def _payload(vendor, **extra):
    return {"vendor_code": vendor.vendor_code, "vendor_name": vendor.legal_name, **extra}


# --- Invitation -------------------------------------------------------------------------

def _create_invite(vendor, email, *, name="", is_admin=True, user=None):
    token = secrets.token_urlsafe(32)
    days = int(SystemSetting.get("INVITE_EXPIRY_DAYS"))
    invite = VendorInvite.objects.create(
        vendor=vendor, email=email.lower(), name=name, is_admin=is_admin, user=user,
        token_hash=VendorInvite.hash_token(token), expires_at=timezone.now() + timedelta(days=days),
    )
    return invite, token


@transaction.atomic
def invite_vendor(*, by, legal_name, email, category):
    _require(by, "vendor.invite")
    email = email.lower().strip()
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError({"email": "A user with this email already exists."})
    vendor = Vendor.objects.create(
        vendor_code=f"V{next_value('vendor_code'):05d}", legal_name=legal_name, category=category,
        contact_email=email, status=Vendor.INVITED, owner_manager=by, invited_by=by,
    )
    invite, token = _create_invite(vendor, email)
    audit.log("vendor.invited", "vendor", vendor.pk, after={"email": email, "category": category})
    checklist = [d.name for d in DocumentType.for_category(category)]
    notify(
        "vendor_invited", to=[email], cc=[by],
        payload=_payload(vendor, expires=invite.expires_at.strftime("%d %b %Y"), checklist=checklist),
        link=reverse("vendor:accept_invite", args=[token]),
    )
    return vendor, token


def find_invite(token):
    return VendorInvite.objects.select_related("vendor", "user").filter(token_hash=VendorInvite.hash_token(token)).first()


@transaction.atomic
def accept_invite(invite, *, password, name=""):
    """Sets the password. For a new vendor this also moves INVITED → DRAFT."""
    if not invite.is_valid:
        raise ValidationError("This invitation link has expired or has already been used.")
    vendor = invite.vendor
    user = invite.user
    if user is None:
        user = User.objects.create_user(invite.email, password, name=name or invite.name or invite.email, user_type=User.VENDOR)
        role = Role.objects.get(code=VENDOR_ADMIN if invite.is_admin else VENDOR_USER)
        UserRole.objects.create(user=user, role=role)
        VendorUser.objects.create(vendor=vendor, user=user, is_admin=invite.is_admin)
    else:
        user.set_password(password)
        user.save()
    invite.used_at = timezone.now()
    invite.save(update_fields=["used_at", "updated_at"])
    audit.log("vendor.invite_accepted", "vendor", vendor.pk, actor=user, after={"email": user.email})
    if vendor.status == Vendor.INVITED:
        _set_status(vendor, Vendor.DRAFT, "vendor.status")
    return user


# --- Registration -----------------------------------------------------------------------

REGISTRATION_FIELDS = [
    "legal_name", "trade_name", "constitution", "msme_status", "udyam_no", "msme_type",
    "contact_name", "contact_phone", "address",
]


@transaction.atomic
def save_registration(vendor, *, by, data):
    _require(by, "vendor.edit_profile")
    if vendor.status not in (Vendor.DRAFT,):
        raise ValidationError("The registration can only be edited while in Draft.")
    before = audit.snapshot(vendor, REGISTRATION_FIELDS) | {"pan_last4": vendor.pan_last4}
    for f in REGISTRATION_FIELDS:
        if f in data:
            setattr(vendor, f, data[f])
    pan = data["pan"].strip().upper()
    gstin = data["gstin"].strip().upper()
    if not valid_pan(pan):
        raise ValidationError({"pan": "Enter a valid PAN (e.g. AAACA1234A)."})
    if not valid_gstin(gstin):
        raise ValidationError({"gstin": "Enter a valid GSTIN (format and checksum)."})
    if pan_from_gstin(gstin) != pan:
        raise ValidationError({"gstin": "The GSTIN does not contain this PAN."})
    if not valid_ifsc(data["ifsc"]):
        raise ValidationError({"ifsc": "Enter a valid IFSC (e.g. HDFC0001234)."})
    vendor.pan = pan
    if data.get("consent"):
        vendor.consent_at = vendor.consent_at or timezone.now()
    vendor.save()
    g = vendor.gstins.filter(is_primary=True).first() or VendorGSTIN(vendor=vendor, is_primary=True)
    g.gstin, g.state_code, g.address = gstin, gstin[:2], data.get("address", "")
    g.save()
    acct = vendor.bank_accounts.filter(status=VendorBankAccount.PENDING).first() or VendorBankAccount(vendor=vendor, requested_by=by)
    acct.holder_name, acct.ifsc, acct.bank_name = data["holder_name"], data["ifsc"].upper(), data.get("bank_name", "")
    if data.get("account_no"):
        acct.account_no = data["account_no"]
    acct.save()
    after = audit.snapshot(vendor, REGISTRATION_FIELDS) | {"pan_last4": vendor.pan_last4}
    b, a = audit.diff(before, after)
    audit.log("vendor.profile_updated", "vendor", vendor.pk, before=b, after=a | {"gstin": gstin, "bank_last4": acct.account_last4})
    return vendor


@transaction.atomic
def upload_document(vendor, *, by, document_type, uploaded, valid_from=None, valid_to=None):
    _require(by, "vendor.edit_profile")
    if vendor.status not in (Vendor.DRAFT,):
        raise ValidationError("Documents can only be uploaded while the registration is in Draft.")
    f = store_upload(uploaded, vendor=vendor, user=by)
    vendor.documents.filter(document_type=document_type, is_current=True).update(is_current=False)
    doc = VendorDocument.objects.create(vendor=vendor, document_type=document_type, file=f, valid_from=valid_from, valid_to=valid_to)
    if document_type.parser_profile != "NONE":
        doc.parse_job = start_parse(f, document_type.parser_profile)
        doc.save(update_fields=["parse_job", "updated_at"])
    audit.log("vendor.document_uploaded", "vendor_document", doc.pk, after={"type": document_type.code, "file": f.original_name})
    return doc


def current_documents(vendor):
    return {d.document_type_id: d for d in vendor.documents.select_related("document_type", "parse_job", "file").filter(is_current=True)}


def parsed_values(vendor):
    """Merge parser output from current KYC docs into registration-form initial values."""
    out = {}
    mapping = {
        "PAN": {"pan": "pan", "name": "legal_name"},
        "GST": {"gstin": "gstin", "legal_name": "legal_name", "trade_name": "trade_name", "address": "address"},
        "CHEQUE": {"account_no": "account_no", "ifsc": "ifsc", "holder_name": "holder_name", "bank_name": "bank_name"},
        "UDYAM": {"udyam_no": "udyam_no", "msme_type": "msme_type"},
    }
    for doc in current_documents(vendor).values():
        job = doc.parse_job
        if job and job.status == job.DONE:
            for src, dst in mapping.get(job.kind, {}).items():
                if job.extracted_json.get(src):
                    out.setdefault(dst, job.extracted_json[src])
    return out


def mismatches(vendor):
    """Fields where the parsed documents disagree with what the vendor typed."""
    typed = {"pan": vendor.pan, "legal_name": vendor.legal_name, "udyam_no": vendor.udyam_no}
    g = vendor.primary_gstin
    typed["gstin"] = g.gstin if g else ""
    acct = vendor.bank_accounts.exclude(status__in=[VendorBankAccount.REJECTED, VendorBankAccount.SUPERSEDED]).order_by("-created_at").first()
    if acct:
        typed.update(account_no=acct.account_no, ifsc=acct.ifsc)
    out = []
    for field, parsed in parsed_values(vendor).items():
        mine = typed.get(field)
        if mine and parsed and str(mine).strip().upper() != str(parsed).strip().upper():
            out.append({"field": field, "typed": mine, "parsed": parsed})
    return out


@transaction.atomic
def submit_registration(vendor, *, by):
    _require(by, "vendor.edit_profile")
    if vendor.status != Vendor.DRAFT:
        raise ValidationError("Only a Draft registration can be submitted.")
    problems = []
    if not vendor.pan_enc or not vendor.primary_gstin or not vendor.bank_accounts.exists():
        problems.append("Complete the registration form (PAN, GSTIN and bank details).")
    if not vendor.consent_at:
        problems.append("Accept the data-processing consent notice.")
    docs = current_documents(vendor)
    missing = [dt.name for dt in vendor.mandatory_document_types() if dt.pk not in docs or docs[dt.pk].status == VendorDocument.REJECTED]
    if missing:
        problems.append("Upload: " + ", ".join(missing))
    if problems:
        raise ValidationError(problems)
    _set_status(vendor, Vendor.SUBMITTED, "vendor.status")
    managers = [vendor.owner_manager] if vendor.owner_manager else list(internal_users_with_role(VENDOR_MANAGER))
    notify("registration_submitted", to=managers, payload=_payload(vendor), link=reverse("console:vendor_detail", args=[vendor.pk]), in_app=managers)
    return vendor


# --- KYC review -------------------------------------------------------------------------

def _not_inviter(vendor, user):
    if vendor.invited_by_id == user.pk:
        raise PermissionDenied("The person who invited a vendor cannot review or activate it.")


def _start_review(vendor):
    if vendor.status == Vendor.SUBMITTED:
        _set_status(vendor, Vendor.UNDER_REVIEW, "vendor.status")


@transaction.atomic
def review_document(doc, *, by, approve, remarks=""):
    _require(by, "vendor.review_kyc")
    vendor = doc.vendor
    _not_inviter(vendor, by)
    if vendor.status not in (Vendor.SUBMITTED, Vendor.UNDER_REVIEW):
        raise ValidationError("Documents can only be reviewed after the vendor submits.")
    if not approve and not remarks.strip():
        raise ValidationError({"remarks": "Remarks are required to reject a document."})
    _start_review(vendor)
    before = {"status": doc.status}
    doc.status = VendorDocument.APPROVED if approve else VendorDocument.REJECTED
    doc.remarks, doc.reviewer = remarks, by
    doc.save()
    audit.log("vendor.document_reviewed", "vendor_document", doc.pk, before=before, after={"status": doc.status, "remarks": remarks})
    return doc


@transaction.atomic
def send_back(vendor, *, by, remarks):
    _require(by, "vendor.review_kyc")
    _not_inviter(vendor, by)
    if vendor.status not in (Vendor.SUBMITTED, Vendor.UNDER_REVIEW):
        raise ValidationError("Only a submitted registration can be sent back.")
    if not remarks.strip():
        raise ValidationError({"remarks": "Remarks are required to send back."})
    _set_status(vendor, Vendor.DRAFT, "vendor.sent_back", review_remarks=remarks)
    doc_remarks = [{"document": d.document_type.name, "remarks": d.remarks} for d in current_documents(vendor).values() if d.status == VendorDocument.REJECTED]
    admins = vendor_admins(vendor)
    notify("kyc_sent_back", to=admins, payload=_payload(vendor, remarks=remarks, documents=doc_remarks), link=reverse("vendor:onboarding"), in_app=admins)


@transaction.atomic
def activate(vendor, *, by):
    _require(by, "vendor.activate")
    _not_inviter(vendor, by)
    if vendor.status not in (Vendor.SUBMITTED, Vendor.UNDER_REVIEW):
        raise ValidationError("Only a submitted vendor can be activated.")
    docs = current_documents(vendor)
    pending = [dt.name for dt in vendor.mandatory_document_types() if dt.pk not in docs or docs[dt.pk].status != VendorDocument.APPROVED]
    if pending:
        raise ValidationError("Approve all mandatory documents first: " + ", ".join(pending))
    acct = vendor.bank_accounts.filter(status=VendorBankAccount.PENDING).order_by("-created_at").first()
    if acct:
        acct.status, acct.verified_at, acct.approved_by = VendorBankAccount.ACTIVE, timezone.now(), by
        acct.save()
        audit.log("bank.activated", "vendor_bank_account", acct.pk, after={"status": acct.status, "last4": acct.account_last4})
    vendor.gstins.filter(verified_at__isnull=True).update(verified_at=timezone.now())
    _set_status(vendor, Vendor.ACTIVE, "vendor.activated", activated_at=timezone.now(), review_remarks="")
    users = vendor_all_users(vendor)
    notify("vendor_activated", to=users, cc=[vendor.owner_manager] if vendor.owner_manager else [], payload=_payload(vendor), link=reverse("vendor:invoice_upload"), in_app=users)


@transaction.atomic
def reject_vendor(vendor, *, by, remarks):
    _require(by, "vendor.activate")
    _not_inviter(vendor, by)
    if vendor.status not in (Vendor.SUBMITTED, Vendor.UNDER_REVIEW):
        raise ValidationError("Only a submitted vendor can be rejected.")
    if not remarks.strip():
        raise ValidationError({"remarks": "A reason is required."})
    _set_status(vendor, Vendor.REJECTED, "vendor.rejected", review_remarks=remarks)
    notify("vendor_rejected", to=vendor_admins(vendor), payload=_payload(vendor, remarks=remarks))


@transaction.atomic
def block(vendor, *, by, reason):
    _require(by, "vendor.block")
    if vendor.status != Vendor.ACTIVE:
        raise ValidationError("Only an active vendor can be blocked.")
    if not reason.strip():
        raise ValidationError({"reason": "A reason is required."})
    _set_status(vendor, Vendor.BLOCKED, "vendor.blocked", block_reason=reason)
    notify("vendor_blocked", to=vendor_admins(vendor), cc=[vendor.owner_manager] if vendor.owner_manager else [], payload=_payload(vendor, reason=reason, contact=by.email))


@transaction.atomic
def unblock(vendor, *, by):
    _require(by, "vendor.block")
    if vendor.status != Vendor.BLOCKED:
        raise ValidationError("Vendor is not blocked.")
    _set_status(vendor, Vendor.ACTIVE, "vendor.unblocked", block_reason="")


# --- Changes after activation -----------------------------------------------------------

@transaction.atomic
def request_bank_change(vendor, *, by, holder_name, account_no, ifsc, bank_name, uploaded=None):
    _require(by, "vendor.edit_bank")
    if vendor.status != Vendor.ACTIVE:
        raise ValidationError("Bank details can be changed only for an active vendor.")
    if not valid_ifsc(ifsc):
        raise ValidationError({"ifsc": "Enter a valid IFSC."})
    if vendor.bank_accounts.filter(status=VendorBankAccount.PENDING).exists():
        raise ValidationError("A bank change is already awaiting verification.")
    latest = vendor.bank_accounts.order_by("-version").first()
    acct = VendorBankAccount(vendor=vendor, holder_name=holder_name, ifsc=ifsc.upper(), bank_name=bank_name, requested_by=by, version=(latest.version + 1 if latest else 1))
    acct.account_no = account_no
    acct.save()
    if uploaded is not None:
        cheque = DocumentType.objects.filter(code="CANCELLED_CHEQUE").first()
        f = store_upload(uploaded, vendor=vendor, user=by)
        vendor.documents.filter(document_type=cheque, is_current=True).update(is_current=False)
        VendorDocument.objects.create(vendor=vendor, document_type=cheque, file=f, parse_job=start_parse(f, "CHEQUE"))
    audit.log("bank.change_requested", "vendor_bank_account", acct.pk, before={"last4": latest.account_last4 if latest else None}, after={"last4": acct.account_last4, "ifsc": acct.ifsc})
    reviewers = list(internal_users_with_role(FINANCE)) + ([vendor.owner_manager] if vendor.owner_manager else [])
    notify("bank_change_requested", to=reviewers, cc=vendor_admins(vendor), payload=_payload(vendor, last4=acct.account_last4), link=reverse("console:bank_changes"), in_app=reviewers)
    return acct


@transaction.atomic
def decide_bank_change(acct, *, by, approve, remarks=""):
    _require(by, "bank.approve")
    if acct.status != VendorBankAccount.PENDING or acct.vendor.status != Vendor.ACTIVE:
        raise ValidationError("This change is not awaiting approval.")
    if acct.requested_by_id == by.pk:
        raise PermissionDenied("You cannot approve a change you requested.")
    if not approve and not remarks.strip():
        raise ValidationError({"remarks": "Remarks are required to reject."})
    vendor = acct.vendor
    if approve:
        vendor.bank_accounts.filter(status=VendorBankAccount.ACTIVE).update(status=VendorBankAccount.SUPERSEDED)
        acct.status, acct.verified_at = VendorBankAccount.ACTIVE, timezone.now()
    else:
        acct.status = VendorBankAccount.REJECTED
    acct.approved_by, acct.decision_remarks = by, remarks
    acct.save()
    audit.log("bank.change_" + ("approved" if approve else "rejected"), "vendor_bank_account", acct.pk, before={"status": "PENDING"}, after={"status": acct.status, "remarks": remarks})
    notify("bank_change_decided", to=vendor_admins(vendor), payload=_payload(vendor, decision="approved" if approve else "rejected", remarks=remarks, last4=acct.account_last4), link=reverse("vendor:bank"))


@transaction.atomic
def add_gstin(vendor, *, by, gstin, address=""):
    _require(by, "vendor.edit_profile")
    gstin = gstin.strip().upper()
    if not valid_gstin(gstin) or pan_from_gstin(gstin) != vendor.pan:
        raise ValidationError({"gstin": "Enter a valid GSTIN that contains your PAN."})
    if vendor.gstins.filter(gstin=gstin).exists():
        raise ValidationError({"gstin": "This GSTIN is already registered."})
    g = VendorGSTIN.objects.create(vendor=vendor, gstin=gstin, state_code=gstin[:2], address=address)
    audit.log("vendor.gstin_added", "vendor_gstin", g.pk, after={"gstin": gstin})
    return g


@transaction.atomic
def verify_gstin(g, *, by):
    _require(by, "vendor.verify_gstin")
    g.verified_at = timezone.now()
    g.save()
    audit.log("vendor.gstin_verified", "vendor_gstin", g.pk, after={"gstin": g.gstin})


# --- Vendor users -----------------------------------------------------------------------

@transaction.atomic
def add_vendor_user(vendor, *, by, email, name, is_admin=False):
    _require(by, "vendor.manage_users")
    email = email.lower().strip()
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError({"email": "A user with this email already exists."})
    invite, token = _create_invite(vendor, email, name=name, is_admin=is_admin)
    audit.log("vendor.user_invited", "vendor", vendor.pk, after={"email": email, "is_admin": is_admin})
    notify("vendor_user_invited", to=[email], payload=_payload(vendor, name=name, expires=invite.expires_at.strftime("%d %b %Y")), link=reverse("vendor:accept_invite", args=[token]))
    return invite, token


@transaction.atomic
def remove_vendor_user(vu, *, by):
    _require(by, "vendor.manage_users")
    if vu.user_id == by.pk:
        raise ValidationError("You cannot remove yourself.")
    vu.is_active = False
    vu.save()
    vu.user.status = User.INACTIVE
    vu.user.save()
    audit.log("vendor.user_removed", "vendor", vu.vendor_id, after={"email": vu.user.email})
