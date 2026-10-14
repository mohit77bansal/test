from django.contrib import admin
from django.urls import include, path

from apps.core.views import healthz

admin.site.site_header = "Vendor Portal — System Admin"

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("sysadmin/", admin.site.urls),
    path("notifications/", include("apps.notify.urls")),
    path("", include("apps.accounts.urls")),
]
