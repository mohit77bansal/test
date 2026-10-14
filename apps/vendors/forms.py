from django import forms

from .models import CATEGORIES, Vendor


class InviteVendorForm(forms.Form):
    legal_name = forms.CharField(max_length=250, label="Vendor name")
    email = forms.EmailField(label="Contact email")
    category = forms.ChoiceField(choices=CATEGORIES)


class RegistrationForm(forms.Form):
    legal_name = forms.CharField(max_length=250)
    trade_name = forms.CharField(max_length=250, required=False)
    constitution = forms.ChoiceField(choices=Vendor.CONSTITUTIONS)
    pan = forms.CharField(max_length=10, label="PAN")
    gstin = forms.CharField(max_length=15, label="Primary GSTIN")
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    contact_name = forms.CharField(max_length=200)
    contact_phone = forms.CharField(max_length=20)
    msme_status = forms.ChoiceField(choices=Vendor.MSME_STATUSES, label="MSME status")
    udyam_no = forms.CharField(max_length=32, required=False, label="Udyam number")
    msme_type = forms.ChoiceField(choices=Vendor.MSME_TYPES, required=False, label="MSME type")
    holder_name = forms.CharField(max_length=200, label="Account holder name")
    account_no = forms.CharField(max_length=20, label="Account number", required=False)
    ifsc = forms.CharField(max_length=11, label="IFSC")
    bank_name = forms.CharField(max_length=200, required=False)
    consent = forms.BooleanField(
        label="I consent to Solfin processing this information for vendor onboarding and payments, as described in the privacy notice (DPDP Act 2023).",
    )

    def __init__(self, *args, account_required=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account_no"].required = account_required

    def clean(self):
        data = super().clean()
        if data.get("msme_status") == "MSME" and not data.get("udyam_no"):
            self.add_error("udyam_no", "Udyam number is required for MSME vendors.")
        return data


class DocumentUploadForm(forms.Form):
    file = forms.FileField()
    valid_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    valid_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))


class BankChangeForm(forms.Form):
    holder_name = forms.CharField(max_length=200)
    account_no = forms.CharField(max_length=20)
    ifsc = forms.CharField(max_length=11, label="IFSC")
    bank_name = forms.CharField(max_length=200)
    cheque = forms.FileField(label="Cancelled cheque", required=False)


class VendorUserForm(forms.Form):
    name = forms.CharField(max_length=200)
    email = forms.EmailField()
    is_admin = forms.BooleanField(required=False, label="Vendor Admin")


class GstinForm(forms.Form):
    gstin = forms.CharField(max_length=15, label="GSTIN")
    address = forms.CharField(required=False)
