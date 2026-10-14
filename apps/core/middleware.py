from django.conf import settings
from django.db import transaction

from .rls import db_context


class VendorIsolationMiddleware:
    """Wraps each request in a transaction and sets the Postgres RLS context with SET LOCAL.

    Vendor users: app.is_internal=off, app.vendor_id=<their vendor>.
    Anonymous:    app.is_internal=off, app.vendor_id=''  (sees no vendor rows).
    Internal:     app.is_internal=on.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        internal = bool(user.is_authenticated and user.is_internal)
        vendor_id = user.vendor_id if user.is_authenticated and not internal else None
        request.vendor_id = vendor_id
        with transaction.atomic(), db_context(internal=internal, vendor_id=vendor_id):
            return self.get_response(request)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        csp = getattr(settings, "CONTENT_SECURITY_POLICY", "")
        if csp and not request.path.startswith("/sysadmin/"):
            response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response
