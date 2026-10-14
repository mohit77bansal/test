from django import forms
from django.contrib.auth import password_validation

from apps.core.models import CostCentre

from .models import Role, User


class LoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)


class OtpForm(forms.Form):
    code = forms.CharField(max_length=6, min_length=6)


class SetPasswordForm(forms.Form):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("The two passwords do not match.")
        if p1:
            password_validation.validate_password(p1, self.user)
        return data


class InternalUserForm(forms.ModelForm):
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.exclude(code__in=["VENDOR_ADMIN", "VENDOR_USER"]),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    l1_cost_centre = forms.ModelChoiceField(
        queryset=CostCentre.objects.filter(is_active=True), required=False, help_text="Scope for the L1 role (blank = all)"
    )

    class Meta:
        model = User
        fields = ["email", "name", "phone", "status"]
