from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404


def apply_errors(form, exc):
    """Attach a service-layer ValidationError to a Django form."""
    if hasattr(exc, "error_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        for msg in exc.messages:
            form.add_error(None, msg)


def vendor_required(view=None, *, active=False):
    """Vendor-portal pages: logged-in vendor user with an active membership."""

    def decorator(fn):
        @wraps(fn)
        def wrapped(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if user.is_internal or user.vendor is None:
                raise PermissionDenied("Vendor portal only")
            request.vendor = user.vendor
            if active and request.vendor.status != "ACTIVE":
                from django.shortcuts import redirect

                return redirect("vendor:home")
            return fn(request, *args, **kwargs)

        return wrapped

    return decorator(view) if view else decorator


def internal_required(fn):
    @wraps(fn)
    def wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user.is_internal:
            raise Http404
        return fn(request, *args, **kwargs)

    return wrapped
