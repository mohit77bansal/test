"""Admin console: approval matrix."""
from django import forms
from django.contrib import messages
from django.shortcuts import redirect, render

from apps.audit import services as audit
from apps.core.permissions import require_perm
from apps.core.views_utils import internal_required

from .models import ApprovalMatrix


class MatrixForm(forms.ModelForm):
    class Meta:
        model = ApprovalMatrix
        fields = ["category", "cost_centre", "l1_max", "l2_max", "requires_l3_above", "is_active"]

    def clean(self):
        data = super().clean()
        if data.get("l1_max") is not None and data.get("l2_max") is not None and data["l1_max"] > data["l2_max"]:
            raise forms.ValidationError("L1 limit must not exceed the L2 limit.")
        return data


MatrixFormSet = forms.modelformset_factory(ApprovalMatrix, form=MatrixForm, extra=1, can_delete=False)


@internal_required
@require_perm("admin.matrix")
def matrix(request):
    formset = MatrixFormSet(request.POST or None, queryset=ApprovalMatrix.objects.order_by("category", "cost_centre__code"))
    if request.method == "POST" and formset.is_valid():
        for form in formset.forms:
            if form.has_changed():
                before = audit.snapshot(form.instance) if form.instance.pk else None
                obj = form.save()
                audit.log("admin.matrix_saved", "approval_matrix", obj.pk, before=before, after=audit.snapshot(obj))
        messages.success(request, "Approval matrix saved. New limits apply to invoices submitted from now on.")
        return redirect("console:admin_matrix")
    return render(request, "console/admin_matrix.html", {"formset": formset})
