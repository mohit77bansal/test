from django.core.paginator import Paginator
from django.shortcuts import render

from apps.core.permissions import require_perm
from apps.core.views_utils import internal_required
from apps.reports.exports import maybe_export

from .models import AuditLog

COLUMNS = [
    ("At", lambda a: a.at),
    ("Actor", lambda a: a.actor.email if a.actor_id and a.actor else ""),
    ("Action", lambda a: a.action),
    ("Entity", lambda a: a.entity),
    ("Entity id", lambda a: a.entity_id),
    ("Before", lambda a: a.before_json),
    ("After", lambda a: a.after_json),
    ("IP", lambda a: a.ip),
]


@internal_required
@require_perm("audit.view")
def audit_log(request):
    qs = AuditLog.objects.select_related("actor").all()
    for param, field in (("action", "action__icontains"), ("entity", "entity"), ("entity_id", "entity_id"), ("actor", "actor__email__icontains")):
        if request.GET.get(param):
            qs = qs.filter(**{field: request.GET[param]})
    export = maybe_export(request, list(qs[:50000]), COLUMNS, "audit-log")
    if export:
        return export
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "console/audit.html", {"page": page, "params": request.GET})
