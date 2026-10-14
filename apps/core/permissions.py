"""Single source of truth for rights: role × action.

Views enforce these with `require_perm` / `PermRequiredMixin`; templates use `{% if user|can:"code" %}`.
`python manage.py permission_matrix` renders this map to docs/PERMISSIONS.md.
Object-level rules (vendor ownership, maker-checker, approval level) live in the services.
"""
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

VENDOR_ADMIN = "VENDOR_ADMIN"
VENDOR_USER = "VENDOR_USER"
VENDOR_MANAGER = "VENDOR_MANAGER"
L1 = "L1"
L2 = "L2"
L3 = "L3"
FINANCE = "FINANCE"
ADMIN = "ADMIN"
AUDITOR = "AUDITOR"

VENDOR_ROLES = (VENDOR_ADMIN, VENDOR_USER)
INTERNAL_ROLES = (VENDOR_MANAGER, L1, L2, L3, FINANCE, ADMIN, AUDITOR)
ALL_ROLES = VENDOR_ROLES + INTERNAL_ROLES
ROLE_NAMES = {
    VENDOR_ADMIN: "Vendor Admin",
    VENDOR_USER: "Vendor User",
    VENDOR_MANAGER: "Vendor Manager",
    L1: "L1 Approver",
    L2: "L2 Approver",
    L3: "L3 Approver",
    FINANCE: "Finance (AP)",
    ADMIN: "Admin",
    AUDITOR: "Auditor",
}

_READERS = {VENDOR_MANAGER, L1, L2, L3, FINANCE, ADMIN, AUDITOR}

PERMISSIONS: dict[str, tuple[str, set[str]]] = {
    # Vendor side
    "vendor.edit_profile": ("Edit vendor profile and KYC", {VENDOR_ADMIN}),
    "vendor.manage_users": ("Add/remove vendor users", {VENDOR_ADMIN}),
    "vendor.edit_bank": ("Request bank detail change", {VENDOR_ADMIN}),
    "invoice.upload": ("Upload and submit invoices", {VENDOR_ADMIN, VENDOR_USER}),
    "invoice.reply": ("Reply to invoice queries", {VENDOR_ADMIN, VENDOR_USER}),
    "invoice.view_own": ("View own invoices and payments", {VENDOR_ADMIN, VENDOR_USER}),
    # Internal side
    "vendor.view_all": ("View all vendors", set(_READERS)),
    "vendor.invite": ("Invite vendors", {VENDOR_MANAGER}),
    "vendor.review_kyc": ("Review KYC documents, send back", {VENDOR_MANAGER}),
    "vendor.activate": ("Activate / reject vendors", {VENDOR_MANAGER}),
    "vendor.block": ("Block / unblock vendors", {VENDOR_MANAGER}),
    "vendor.verify_gstin": ("Verify a changed GSTIN", {VENDOR_MANAGER}),
    "invoice.view_all": ("View all invoices", set(_READERS)),
    "invoice.fin_precheck": ("Finance pre-check", {FINANCE}),
    "invoice.approve_l1": ("Approve / reject / query at L1", {L1}),
    "invoice.approve_l2": ("Approve / reject / query at L2", {L2}),
    "invoice.approve_l3": ("Approve / reject / query at L3", {L3}),
    "payment.record": ("Record payments (UTR, TDS)", {FINANCE}),
    "bank.approve": ("Approve bank-detail changes", {FINANCE}),
    "report.view": ("View reports", set(_READERS)),
    "report.export": ("Export CSV / Excel", {VENDOR_MANAGER, L1, L2, L3, FINANCE, ADMIN, AUDITOR}),
    "file.view": ("Download vendor files", set(_READERS)),
    "admin.users": ("Manage internal users and roles", {ADMIN}),
    "admin.matrix": ("Edit approval matrix", {ADMIN}),
    "admin.checklist": ("Edit document checklist", {ADMIN}),
    "admin.templates": ("Switch email templates on/off", {ADMIN}),
    "audit.view": ("View audit log", {ADMIN, AUDITOR}),
}

LEVEL_PERMS = {"FIN_PRE": "invoice.fin_precheck", "L1": "invoice.approve_l1", "L2": "invoice.approve_l2", "L3": "invoice.approve_l3"}


def roles_for(code: str) -> set[str]:
    return PERMISSIONS[code][1]


def has_perm(user, code: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    if code not in PERMISSIONS:
        raise KeyError(f"Unknown permission {code}")
    return bool(user.role_codes & roles_for(code))


def require_perm(code):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"/login/?next={request.path}")
            if not has_perm(request.user, code):
                raise PermissionDenied(code)
            return view(request, *args, **kwargs)

        wrapped.required_perm = code
        return wrapped

    return decorator


def matrix_markdown() -> str:
    header = "| Permission | Action | " + " | ".join(ALL_ROLES) + " |"
    sep = "|" + "---|" * (len(ALL_ROLES) + 2)
    lines = [
        "# Permission matrix",
        "",
        "Generated from `apps/core/permissions.py` by `python manage.py permission_matrix`. Do not edit by hand.",
        "",
        "Object-level rules on top of this table: vendor users only ever see their own vendor (queryset + RLS); "
        "nobody approves an invoice they uploaded or already acted on at an earlier level; the vendor's inviter "
        "cannot approve its KYC or activate it; a bank change cannot be approved by its requester.",
        "",
        header,
        sep,
    ]
    for code, (label, roles) in PERMISSIONS.items():
        cells = " | ".join("✓" if r in roles else "" for r in ALL_ROLES)
        lines.append(f"| `{code}` | {label} | {cells} |")
    return "\n".join(lines) + "\n"
