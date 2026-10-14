"""Admin console: internal users and roles."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from apps.audit import services as audit
from apps.core.permissions import require_perm
from apps.core.views_utils import internal_required

from .forms import InternalUserForm
from .models import Role, User, UserRole


@internal_required
@require_perm("admin.users")
def user_list(request):
    users = User.objects.filter(user_type=User.INTERNAL).prefetch_related("user_roles__role", "user_roles__cost_centre")
    return render(request, "console/admin_users.html", {"users": users})


@internal_required
@require_perm("admin.users")
def user_edit(request, pk=None):
    user = get_object_or_404(User, pk=pk, user_type=User.INTERNAL) if pk else None
    initial = {}
    if user:
        initial["roles"] = Role.objects.filter(user_roles__user=user).distinct()
        scoped = user.user_roles.filter(role__code="L1", cost_centre__isnull=False).first()
        initial["l1_cost_centre"] = scoped.cost_centre if scoped else None
    form = InternalUserForm(request.POST or None, instance=user, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                before = sorted(user.role_codes) if user else []
                obj = form.save(commit=False)
                obj.email = obj.email.lower()
                if not user:
                    obj.user_type = User.INTERNAL
                    obj.set_unusable_password()
                obj.save()
                UserRole.objects.filter(user=obj).exclude(role__code__in=["VENDOR_ADMIN", "VENDOR_USER"]).delete()
                for role in form.cleaned_data["roles"]:
                    cc = form.cleaned_data.get("l1_cost_centre") if role.code == "L1" else None
                    UserRole.objects.create(user=obj, role=role, cost_centre=cc)
                after = sorted(r.code for r in form.cleaned_data["roles"])
                audit.log("admin.user_saved", "user", obj.pk, before={"roles": before}, after={"email": obj.email, "status": obj.status, "roles": after})
            messages.success(request, f"Saved {obj.email}." + ("" if user else " They can set a password with 'Forgot password'."))
            return redirect("console:admin_users")
        except ValidationError as e:
            form.add_error(None, e)
    return render(request, "console/admin_user_form.html", {"form": form, "obj": user})
