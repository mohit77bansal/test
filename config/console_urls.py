from django.urls import path

from apps.accounts import console_views as users
from apps.audit import views as audit
from apps.invoices import admin_views as inv_admin
from apps.invoices import console_views as inv
from apps.notify import admin_views as tpl
from apps.reports import views as reports
from apps.vendors import admin_views as vend_admin
from apps.vendors import console_views as vend

app_name = "console"
urlpatterns = [
    path("", inv.home, name="home"),
    path("vendors/", vend.vendor_list, name="vendors"),
    path("vendors/invite/", vend.vendor_invite, name="vendor_invite"),
    path("vendors/<uuid:pk>/", vend.vendor_detail, name="vendor_detail"),
    path("vendors/<uuid:pk>/documents/<uuid:doc_pk>/review/", vend.review_document, name="review_document"),
    path("vendors/<uuid:pk>/gstins/<uuid:gstin_pk>/verify/", vend.verify_gstin, name="verify_gstin"),
    path("vendors/<uuid:pk>/<str:action>/", vend.vendor_action, name="vendor_action"),
    path("bank-changes/", vend.bank_changes, name="bank_changes"),
    path("bank-changes/<uuid:pk>/decide/", vend.bank_decide, name="bank_decide"),
    path("invoices/", inv.invoice_list, name="invoices"),
    path("invoices/<uuid:pk>/", inv.invoice_detail, name="invoice_detail"),
    path("invoices/<uuid:pk>/pay/", inv.record_payment, name="record_payment"),
    path("payments/queue/", inv.payment_queue, name="payment_queue"),
    path("files/<uuid:pk>/", inv.file_download, name="file"),
    path("reports/<str:name>/", reports.report, name="report"),
    path("admin/users/", users.user_list, name="admin_users"),
    path("admin/users/new/", users.user_edit, name="admin_user_new"),
    path("admin/users/<uuid:pk>/", users.user_edit, name="admin_user_edit"),
    path("admin/matrix/", inv_admin.matrix, name="admin_matrix"),
    path("admin/documents/", vend_admin.checklist, name="admin_documents"),
    path("admin/templates/", tpl.templates, name="admin_templates"),
    path("admin/templates/<str:code>/toggle/", tpl.toggle, name="admin_template_toggle"),
    path("audit/", audit.audit_log, name="audit"),
]
