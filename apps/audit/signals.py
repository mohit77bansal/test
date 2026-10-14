from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from django.utils import timezone

from . import services


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    user.last_login_at = timezone.now()
    user.save(update_fields=["last_login_at"])
    services.log("auth.login", "user", user.pk, actor=user)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        services.log("auth.logout", "user", user.pk, actor=user)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    services.log("auth.login_failed", "user", "", after={"email": credentials.get("email") or credentials.get("username")})
