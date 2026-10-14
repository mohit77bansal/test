from django.conf import settings


def portal(request):
    user = getattr(request, "user", None)
    unread = 0
    if user is not None and user.is_authenticated:
        from apps.notify.models import Notification

        unread = Notification.objects.filter(user=user, channel="IN_APP", read_at__isnull=True).count()
    return {"SITE_URL": settings.SITE_URL, "unread_count": unread}
