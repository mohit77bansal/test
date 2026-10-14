from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import PasswordResetForm
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST

from apps.audit import services as audit
from apps.notify.services import notify

from .forms import LoginForm, OtpForm
from .models import LoginOTP, User

BACKEND = "django.contrib.auth.backends.ModelBackend"


def landing_url(user):
    return reverse("console:home") if user.is_internal else reverse("vendor:home")


def landing(request):
    if request.user.is_authenticated:
        return redirect(landing_url(request.user))
    return redirect("accounts:login")


def _safe_next(request):
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else ""


def login_view(request):
    if request.user.is_authenticated:
        return redirect(landing_url(request.user))
    form = LoginForm(request.POST or None)
    error = None
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        user = authenticate(request, email=email, password=form.cleaned_data["password"])
        if getattr(request, "axes_locked_out", False):
            return render(request, "accounts/locked.html", status=429)
        if user is None:
            error = "Invalid email or password."
        elif settings.OTP_ENABLED and user.mfa_enabled:
            otp, code = LoginOTP.issue(user)
            request.session["otp_id"] = str(otp.pk)
            request.session["otp_next"] = _safe_next(request)
            notify("login_otp", to=[user], payload={"name": user.name, "code": code, "minutes": settings.OTP_TTL_SECONDS // 60})
            return redirect("accounts:otp")
        else:
            login(request, user, backend=BACKEND)
            return redirect(_safe_next(request) or landing_url(user))
    return render(request, "accounts/login.html", {"form": form, "error": error, "next": _safe_next(request)})


def otp_view(request):
    otp_id = request.session.get("otp_id")
    otp = LoginOTP.objects.select_related("user").filter(pk=otp_id).first() if otp_id else None
    if otp is None or otp.used_at:
        return redirect("accounts:login")
    form = OtpForm(request.POST or None)
    error = None
    if request.method == "POST" and form.is_valid():
        if otp.verify(form.cleaned_data["code"]):
            nxt = request.session.pop("otp_next", "")
            request.session.pop("otp_id", None)
            login(request, otp.user, backend=BACKEND)
            return redirect(nxt or landing_url(otp.user))
        audit.log("auth.otp_failed", "user", otp.user_id, actor=otp.user)
        if otp.exhausted:
            request.session.pop("otp_id", None)
            messages.error(request, "Too many wrong codes. Please sign in again.")
            return redirect("accounts:login")
        error = "That code is not correct."
    return render(request, "accounts/otp.html", {"form": form, "error": error, "email": otp.user.email})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "You have been signed out.")
    return redirect("accounts:login")


class NotifyPasswordResetForm(PasswordResetForm):
    """Routes the reset email through notify() (Solfin email API) instead of Django's mailer."""

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email, html_email_template_name=None):
        link = reverse("accounts:password_reset_confirm", kwargs={"uidb64": context["uid"], "token": context["token"]})
        notify("password_reset", to=[to_email], payload={"name": context["user"].name}, link=link)

    def get_users(self, email):
        return User.objects.filter(email__iexact=email, status=User.ACTIVE)


class PasswordResetView(auth_views.PasswordResetView):
    form_class = NotifyPasswordResetForm
    template_name = "accounts/password_reset.html"
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        audit.log("auth.password_reset", "user", form.user.pk, actor=form.user)
        return response
