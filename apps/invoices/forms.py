from decimal import Decimal

from django import forms

from apps.core.models import CostCentre

from .models import Invoice


class UploadForm(forms.Form):
    file = forms.FileField(label="Invoice file (PDF, JPG or PNG, up to 10 MB)")


class InvoiceForm(forms.Form):
    supplier_gstin = forms.CharField(max_length=15, label="Supplier GSTIN")
    invoice_no = forms.CharField(max_length=50, label="Invoice number")
    invoice_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    cost_centre = forms.ModelChoiceField(queryset=CostCentre.objects.filter(is_active=True), required=False)
    taxable = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), label="Taxable value")
    cgst = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False, label="CGST")
    sgst = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False, label="SGST")
    igst = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False, label="IGST")
    total = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))

    def clean(self):
        data = super().clean()
        for f in ("cgst", "sgst", "igst"):
            data[f] = data.get(f) or Decimal("0")
        return data


class ActionForm(forms.Form):
    action = forms.ChoiceField(choices=[("APPROVE", "Approve"), ("REJECT", "Reject"), ("QUERY", "Raise query")])
    comment = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    tds_section = forms.ChoiceField(choices=Invoice.TDS_SECTIONS, required=False, label="TDS section")
    tds_amount = forms.DecimalField(max_digits=14, decimal_places=2, required=False, min_value=Decimal("0"), label="TDS amount")
    cost_centre = forms.ModelChoiceField(queryset=CostCentre.objects.filter(is_active=True), required=False)


class ReplyForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Reply")
    file = forms.FileField(required=False, label="Attach a corrected invoice or document (optional)")


class PaymentForm(forms.Form):
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    tds_deducted = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False, label="TDS deducted")
    paid_on = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    utr = forms.CharField(max_length=40, label="UTR")
