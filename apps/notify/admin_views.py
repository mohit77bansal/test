from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit import services as audit
from apps.core.permissions import require_perm
from apps.core.views_utils import internal_required

from .models import EmailTemplate


@internal_required
@require_perm("admin.templates")
def templates(request):
    return render(request, "console/admin_templates.html", {"templates": EmailTemplate.objects.all()})


@internal_required
@require_perm("admin.templates")
@require_POST
def toggle(request, code):
    tpl = get_object_or_404(EmailTemplate, code=code)
    if tpl.system:
        messages.error(request, "Security emails cannot be switched off.")
    else:
        tpl.enabled = not tpl.enabled
        tpl.save()
        audit.log("admin.template_toggled", "email_template", tpl.code, before={"enabled": not tpl.enabled}, after={"enabled": tpl.enabled})
        messages.success(request, f"{tpl.name} switched {'on' if tpl.enabled else 'off'}.")
    return redirect("console:admin_templates")
