from django.contrib import admin
from django.urls import include, path

from apps.core.views import healthz

admin.site.site_header = "Vendor Portal — System Admin"

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("sysadmin/", admin.site.urls),
    path("vendor/", include("config.vendor_urls")),
    path("console/", include("config.console_urls")),
    path("notifications/", include("apps.notify.urls")),
    path("", include("apps.accounts.urls")),
]

handler403 = "apps.core.views.forbidden"
handler404 = "apps.core.views.not_found"
