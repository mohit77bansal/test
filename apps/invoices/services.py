"""Invoice state machine (docs/ARCHITECTURE.md §5).

Every transition writes an ApprovalAction and an AuditLog row in the same transaction; emails are
queued on commit. Views never change `status` directly.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.audit import services as audit
from apps.core.context import acting_as
from apps.core.models import CostCentre, SystemSetting
from apps.core.permissions import FINANCE, L1, L2, L3, LEVEL_PERMS, has_perm
from apps.core.sequences import next_value
from apps.notify.services import notify
from apps.parsing.services import start_parse, store_upload
from apps.parsing.validators import financial_year, totals_reconcile
from apps.vendors.models import Vendor
from apps.vendors.services import vendor_admins

from .models import LEVEL_ORDER, ApprovalAction, ApprovalMatrix, Invoice, InvoiceLineItem, InvoiceMessage, Level, Payment

S = Invoice.Status
LEVEL_STATUS = {Level.FIN_PRE: S.FIN_PRECHECK, Level.L1: S.PENDING_L1, Level.L2: S.PENDING_L2, Level.L3: S.PENDING_L3}
LEVEL_LABEL = {Level.FIN_PRE: "Finance pre-check", Level.L1: "L1 approval", Level.L2: "L2 approval", Level.L3: "L3 approval"}


def _payload(inv, **extra):
    return {
        "vendor_code": inv.vendor.vendor_code, "vendor_name": inv.vendor.legal_name, "portal_ref": inv.portal_ref or "",
        "invoice_no": inv.invoice_no, "total": str(inv.total), "status": inv.get_status_display(), **extra,
    }


def _vendor_link(inv):
    return reverse("vendor:invoice_detail", args=[inv.pk])


def _console_link(inv):
    return reverse("console:invoice_detail", args=[inv.pk])


def _log(inv, before_status, action, level, by, comment=""):
    ApprovalAction.objects.create(vendor=inv.vendor, invoice=inv, level=level, actor=by, action=action, comment=comment)
    audit.log(f"invoice.{action.lower()}", "invoice", inv.pk, before={"status": before_status}, after={"status": inv.status, "level": level, "comment": comment})


# --- Upload and submit ------------------------------------------------------------------

@acting_as
@transaction.atomic
def create_from_upload(vendor, *, by, uploaded):
    if not has_perm(by, "invoice.upload"):
        raise PermissionDenied
    if vendor.status != Vendor.ACTIVE:
        raise ValidationError("Invoices can be uploaded only once your vendor account is active.")
    f = store_upload(uploaded, vendor=vendor, user=by)
    inv = Invoice.objects.create(vendor=vendor, file=f, status=S.DRAFT, submitted_by=by)
    inv.parse_job = start_parse(f, "INVOICE")
    inv.save(update_fields=["parse_job", "updated_at"])
    audit.log("invoice.uploaded", "invoice", inv.pk, after={"file": f.original_name, "sha256": f.sha256})
    return inv


def parsed_initial(inv):
    """Form initial values from the parse job (never auto-submitted)."""
    job = inv.parse_job
    if not job or job.status != job.DONE:
        return {}, set(), []
    data = job.extracted_json or {}
    initial = {k: data.get(k) for k in ("invoice_no", "invoice_date", "taxable", "cgst", "sgst", "igst", "total") if data.get(k) not in (None, "")}
    if data.get("supplier_gstin"):
        initial["supplier_gstin"] = data["supplier_gstin"]
    return initial, job.low_confidence_fields(), data.get("line_items") or []


def validate(inv, data):
    """Validations on submit. `data` holds cleaned form values. Returns the matched VendorGSTIN."""
    errors = {}
    vendor = inv.vendor
    gstin = (data.get("supplier_gstin") or "").strip().upper()
    g = vendor.gstins.filter(gstin=gstin).first()
    if g is None:
        errors["supplier_gstin"] = "The GSTIN on the invoice does not match your vendor master."
    elif g.verified_at is None:
        errors["supplier_gstin"] = "This GSTIN is awaiting verification by the Vendor Manager."
    tol = Decimal(str(SystemSetting.get("TOTALS_TOLERANCE")))
    if not totals_reconcile(data.get("taxable"), data.get("cgst"), data.get("sgst"), data.get("igst"), data.get("total"), tol):
        errors["total"] = f"Taxable value + GST must equal the total (±₹{tol})."
    if not data.get("cost_centre"):
        errors["cost_centre"] = "A cost centre is required (all invoices are non-PO)."
    d = data.get("invoice_date")
    if d and d > date.today():
        errors["invoice_date"] = "The invoice date cannot be in the future."
    if d and data.get("invoice_no"):
        dup = Invoice.objects.filter(vendor=vendor, invoice_no__iexact=data["invoice_no"].strip(), fy=financial_year(d)).exclude(pk=inv.pk).exclude(status__in=[S.REJECTED, S.DRAFT])
        if dup.exists():
            errors["invoice_no"] = f"Invoice {data['invoice_no']} has already been submitted for FY {financial_year(d)}."
    if errors:
        raise ValidationError(errors)
    return g


def _required_level(inv):
    matrix = ApprovalMatrix.resolve(inv.vendor.category, inv.cost_centre)
    if matrix is None:
        raise ValidationError("No approval matrix is configured. Contact the administrator.")
    return matrix.required_level(inv.total)


@acting_as
@transaction.atomic
def submit(inv, *, by, data, line_items=()):
    if not has_perm(by, "invoice.upload"):
        raise PermissionDenied
    inv = Invoice.objects.select_for_update().get(pk=inv.pk)
    if inv.status != S.DRAFT:
        raise ValidationError("This invoice has already been submitted.")
    if inv.vendor.status != Vendor.ACTIVE:
        raise ValidationError("Your vendor account is not active.")
    g = validate(inv, data)
    inv.gstin = g
    for f in ("invoice_no", "invoice_date", "taxable", "cgst", "sgst", "igst", "total", "cost_centre"):
        setattr(inv, f, data.get(f) if data.get(f) is not None else getattr(inv, f))
    inv.invoice_no = inv.invoice_no.strip()
    inv.fy = financial_year(inv.invoice_date)
    inv.required_level = _required_level(inv)
    inv.portal_ref = f"INV-{date.today().year}-{next_value('invoice_ref'):05d}"
    inv.submitted_by, inv.submitted_at = by, timezone.now()
    inv.line_items.all().delete()
    for li in line_items:
        if any(li.get(k) for k in ("description", "amount")):
            InvoiceLineItem.objects.create(vendor=inv.vendor, invoice=inv, **{k: li.get(k) for k in ("description", "hsn_sac", "qty", "rate", "amount", "tax_rate")})
    inv.status = S.SUBMITTED
    inv.save()
    _log(inv, S.DRAFT, ApprovalAction.Action.SUBMIT, "VENDOR", by)
    _move_to(inv, Level.FIN_PRE, by=by, action_label="submitted")
    to = [by] + [u for u in vendor_admins(inv.vendor) if u != by]
    from django.conf import settings

    notify("invoice_submitted", to=to, cc=[settings.AP_INBOX_EMAIL], payload=_payload(inv), link=_vendor_link(inv))
    return inv


# --- Approvers --------------------------------------------------------------------------

def level_pool(inv, level):
    users = User.objects.filter(user_type=User.INTERNAL, status=User.ACTIVE)
    if level == Level.FIN_PRE:
        return list(users.filter(user_roles__role__code=FINANCE).distinct())
    if level == Level.L1:
        cc = inv.cost_centre
        q = Q(user_roles__role__code=L1) & (Q(user_roles__cost_centre=cc) | Q(user_roles__cost_centre__isnull=True))
        pool = list(users.filter(q).distinct())
        if cc and cc.l1_owner and cc.l1_owner.has_role(L1) and cc.l1_owner not in pool:
            pool.insert(0, cc.l1_owner)
        return pool
    return list(users.filter(user_roles__role__code={Level.L2: L2, Level.L3: L3}[level]).distinct())


def _earlier_actors(inv):
    return set(inv.actions.filter(action=ApprovalAction.Action.APPROVE).values_list("actor_id", flat=True))


def check_can_act(inv, user):
    """Raise PermissionDenied with a reason if `user` may not act on `inv` now."""
    level = inv.current_level
    if inv.status not in LEVEL_STATUS.values() or not level:
        raise PermissionDenied("This invoice is not awaiting approval.")
    if not has_perm(user, LEVEL_PERMS[level]):
        raise PermissionDenied(f"You do not have the {LEVEL_LABEL[level]} role.")
    if level != Level.FIN_PRE and LEVEL_ORDER.index(level) > LEVEL_ORDER.index(inv.required_level):
        raise PermissionDenied("This invoice does not need this level.")
    if inv.submitted_by_id == user.pk or inv.created_by_id == user.pk:
        raise PermissionDenied("You cannot approve an invoice you uploaded.")
    if user.pk in _earlier_actors(inv):
        raise PermissionDenied("You already approved this invoice at an earlier level.")
    if level == Level.L1 and user not in level_pool(inv, Level.L1):
        raise PermissionDenied("This invoice is routed to another cost-centre owner.")


def can_act(inv, user):
    try:
        check_can_act(inv, user)
        return True, ""
    except PermissionDenied as e:
        return False, str(e)


def _move_to(inv, level, *, by, action_label):
    before = inv.status
    inv.current_level = level
    inv.status = LEVEL_STATUS[level]
    inv.save()
    audit.log("invoice.status", "invoice", inv.pk, before={"status": before}, after={"status": inv.status})
    pool = [u for u in level_pool(inv, level) if u.pk != inv.submitted_by_id and u.pk not in _earlier_actors(inv)]
    notify("approval_pending", to=pool, payload=_payload(inv, level_label=LEVEL_LABEL[level]), link=_console_link(inv), in_app=pool)


@acting_as
@transaction.atomic
def act(inv, *, by, action, comment="", fin_data=None):
    """Approve / reject / query at the invoice's current level."""
    inv = Invoice.objects.select_for_update(of=("self",)).select_related("vendor", "cost_centre").get(pk=inv.pk)
    check_can_act(inv, by)
    comment = (comment or "").strip()
    level = inv.current_level
    before = inv.status
    A = ApprovalAction.Action
    if action in (A.REJECT, A.QUERY) and not comment:
        raise ValidationError({"comment": "A comment is required to reject or raise a query."})
    if action == A.APPROVE:
        if level == Level.FIN_PRE:
            fin_data = fin_data or {}
            if not fin_data.get("tds_section"):
                raise ValidationError({"tds_section": "Select the TDS section (or Not applicable)."})
            inv.tds_section = fin_data["tds_section"]
            inv.tds_amount = fin_data.get("tds_amount") or Decimal("0")
            if fin_data.get("cost_centre") and fin_data["cost_centre"] != inv.cost_centre:
                inv.cost_centre = fin_data["cost_centre"]
                inv.required_level = _required_level(inv)
            inv.save()
            _log(inv, before, A.APPROVE, level, by, comment)
            _move_to(inv, Level.L1, by=by, action_label="approved")
        elif level == inv.required_level:
            inv.status, inv.current_level = S.APPROVED, ""
            inv.approved_at = timezone.now()
            inv.due_date = date.today() + timedelta(days=int(SystemSetting.get("PAYMENT_TERMS_DAYS")))
            inv.save()
            _log(inv, before, A.APPROVE, level, by, comment)
            to = _vendor_recipients(inv)
            notify("invoice_approved", to=to, payload=_payload(inv, due_date=inv.due_date.strftime("%d %b %Y")), link=_vendor_link(inv), in_app=to)
        else:
            _log(inv, before, A.APPROVE, level, by, comment)
            _move_to(inv, LEVEL_ORDER[LEVEL_ORDER.index(level) + 1], by=by, action_label="approved")
    elif action == A.REJECT:
        inv.status, inv.current_level = S.REJECTED, ""
        inv.save()
        _log(inv, before, A.REJECT, level, by, comment)
        to = _vendor_recipients(inv)
        notify("invoice_rejected", to=to, payload=_payload(inv, reason=comment), link=reverse("vendor:invoice_upload"), in_app=to)
    elif action == A.QUERY:
        inv.status, inv.query_level = S.QUERY_RAISED, level
        inv.save()
        _log(inv, before, A.QUERY, level, by, comment)
        InvoiceMessage.objects.create(vendor=inv.vendor, invoice=inv, from_user=by, body=comment)
        to = _vendor_recipients(inv)
        notify("query_raised", to=to, cc=[by], payload=_payload(inv, query=comment, level_label=LEVEL_LABEL[level]), link=_vendor_link(inv), in_app=to)
    else:
        raise ValidationError("Unknown action.")
    return inv


def _vendor_recipients(inv):
    out = [inv.submitted_by] if inv.submitted_by else []
    return out + [u for u in vendor_admins(inv.vendor) if u not in out]


@acting_as
@transaction.atomic
def vendor_reply(inv, *, by, body, uploaded=None):
    if not has_perm(by, "invoice.reply"):
        raise PermissionDenied
    inv = Invoice.objects.select_for_update().get(pk=inv.pk)
    if inv.status != S.QUERY_RAISED:
        raise ValidationError("There is no open query on this invoice.")
    if not (body or "").strip():
        raise ValidationError({"body": "Enter a reply."})
    f = store_upload(uploaded, vendor=inv.vendor, user=by) if uploaded else None
    InvoiceMessage.objects.create(vendor=inv.vendor, invoice=inv, from_user=by, body=body.strip(), file=f)
    level = inv.query_level
    raiser = inv.actions.filter(action=ApprovalAction.Action.QUERY, level=level).order_by("-acted_at").first()
    before = inv.status
    inv.status, inv.current_level, inv.query_level = LEVEL_STATUS[level], level, ""
    inv.save()
    _log(inv, before, ApprovalAction.Action.REPLY, "VENDOR", by, body.strip())
    if raiser:
        notify("vendor_replied", to=[raiser.actor], payload=_payload(inv, reply=body.strip()), link=_console_link(inv), in_app=[raiser.actor])
    return inv


@acting_as
def post_internal_message(inv, *, by, body):
    if not (body or "").strip():
        raise ValidationError({"body": "Enter a message."})
    InvoiceMessage.objects.create(vendor=inv.vendor, invoice=inv, from_user=by, body=body.strip())
    audit.log("invoice.message", "invoice", inv.pk, after={"body": body.strip()[:200]})


# --- Payments ---------------------------------------------------------------------------

@acting_as
@transaction.atomic
def record_payment(inv, *, by, amount, paid_on, utr, tds_deducted=Decimal("0")):
    if not has_perm(by, "payment.record"):
        raise PermissionDenied
    inv = Invoice.objects.select_for_update(of=("self",)).select_related("vendor").get(pk=inv.pk)
    if inv.status not in (S.APPROVED, S.PARTLY_PAID):
        raise ValidationError("Only approved invoices can be paid.")
    if inv.vendor.has_pending_bank_change:
        raise ValidationError("Payments are on hold until Finance approves the vendor's bank-detail change.")
    amount, tds_deducted = Decimal(amount), Decimal(tds_deducted or 0)
    if amount <= 0 or tds_deducted < 0:
        raise ValidationError({"amount": "Enter a positive amount."})
    if amount + tds_deducted > inv.outstanding:
        raise ValidationError({"amount": f"Amount + TDS exceeds the outstanding {inv.outstanding}."})
    if not utr.strip():
        raise ValidationError({"utr": "UTR is required."})
    p = Payment.objects.create(vendor=inv.vendor, invoice=inv, amount=amount, paid_on=paid_on, utr=utr.strip(), tds_deducted=tds_deducted, recorded_by=by)
    before = inv.status
    inv.status = S.PAID if inv.outstanding <= 0 else S.PARTLY_PAID
    inv.save()
    audit.log("payment.recorded", "payment", p.pk, before={"status": before}, after={"invoice": inv.portal_ref, "amount": amount, "utr": p.utr, "tds": tds_deducted, "status": inv.status})
    admins = vendor_admins(inv.vendor)
    notify(
        "payment_recorded", to=admins, cc=[inv.submitted_by] if inv.submitted_by else [],
        payload=_payload(inv, amount=str(amount), utr=p.utr, paid_on=paid_on.strftime("%d %b %Y") if hasattr(paid_on, "strftime") else str(paid_on), tds=str(tds_deducted), outstanding=str(inv.outstanding)),
        link=_vendor_link(inv), in_app=admins,
    )
    return p


# --- Queues -----------------------------------------------------------------------------

def queue_for(user):
    """Invoices `user` can act on now."""
    qs = Invoice.objects.select_related("vendor", "cost_centre").exclude(submitted_by=user)
    qs = qs.exclude(actions__actor=user, actions__action=ApprovalAction.Action.APPROVE)
    q = Q(pk__in=[])
    if user.has_role(FINANCE):
        q |= Q(status=S.FIN_PRECHECK)
    if user.has_role(L1):
        scoped = list(user.user_roles.filter(role__code=L1).values_list("cost_centre_id", flat=True))
        if None in scoped:
            q |= Q(status=S.PENDING_L1)
        else:
            owned = list(CostCentre.objects.filter(l1_owner=user).values_list("pk", flat=True))
            q |= Q(status=S.PENDING_L1, cost_centre_id__in=scoped + owned)
    if user.has_role(L2):
        q |= Q(status=S.PENDING_L2)
    if user.has_role(L3):
        q |= Q(status=S.PENDING_L3)
    return qs.filter(q).distinct().order_by("submitted_at")


def pending_digest():
    users = User.objects.filter(user_type=User.INTERNAL, status=User.ACTIVE, user_roles__role__code__in=[FINANCE, L1, L2, L3]).distinct()
    for u in users:
        items = [{"portal_ref": i.portal_ref, "vendor": i.vendor.legal_name, "total": str(i.total), "since": i.submitted_at.strftime("%d %b")} for i in queue_for(u)]
        if items:
            yield u, items
