"""Admin console: document checklist."""
from django import forms
from django.contrib import messages
from django.shortcuts import redirect, render

from apps.audit import services as audit
from apps.core.permissions import require_perm
from apps.core.views_utils import internal_required

from .models import CATEGORIES, DocumentType


class DocumentTypeForm(forms.ModelForm):
    applies_to = forms.MultipleChoiceField(choices=CATEGORIES, widget=forms.CheckboxSelectMultiple, required=False)

    class Meta:
        model = DocumentType
        fields = ["code", "name", "applies_to", "mandatory", "has_expiry", "parser_profile", "is_active", "sort_order"]


DocumentTypeFormSet = forms.modelformset_factory(DocumentType, form=DocumentTypeForm, extra=1)


@internal_required
@require_perm("admin.checklist")
def checklist(request):
    formset = DocumentTypeFormSet(request.POST or None, queryset=DocumentType.objects.all())
    if request.method == "POST" and formset.is_valid():
        for form in formset.forms:
            if form.has_changed():
                before = audit.snapshot(form.instance) if form.instance.pk else None
                obj = form.save()
                audit.log("admin.checklist_saved", "document_type", obj.pk, before=before, after=audit.snapshot(obj))
        messages.success(request, "Document checklist saved.")
        return redirect("console:admin_documents")
    return render(request, "console/admin_documents.html", {"formset": formset})
