from django.urls import path

from apps.invoices import vendor_views as inv
from apps.vendors import portal_views as v

app_name = "vendor"
urlpatterns = [
    path("", v.home, name="home"),
    path("invite/<str:token>/", v.accept_invite, name="accept_invite"),
    path("onboarding/", v.onboarding, name="onboarding"),
    path("onboarding/documents/", v.documents, name="documents"),
    path("onboarding/documents/<str:code>/upload/", v.upload_document, name="upload_document"),
    path("onboarding/submit/", v.submit_registration, name="submit_registration"),
    path("profile/", v.profile, name="profile"),
    path("users/", v.users, name="users"),
    path("users/<uuid:pk>/remove/", v.remove_user, name="remove_user"),
    path("bank/", v.bank, name="bank"),
    path("files/<uuid:pk>/", v.file_download, name="file"),
    path("invoices/", inv.invoice_list, name="invoices"),
    path("invoices/upload/", inv.upload, name="invoice_upload"),
    path("invoices/<uuid:pk>/", inv.detail, name="invoice_detail"),
    path("invoices/<uuid:pk>/edit/", inv.edit, name="invoice_edit"),
    path("invoices/<uuid:pk>/parse-status/", inv.parse_status, name="invoice_parse_status"),
    path("invoices/<uuid:pk>/discard/", inv.discard, name="invoice_discard"),
    path("payments/", inv.payments, name="payments"),
]
