import logging

from celery import shared_task
from django.utils import timezone

from .models import Notification
from .senders import get_sender
from .services import render

log = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30, retry_backoff=True)
def send_notification(self, notification_id):
    n = Notification.objects.get(pk=notification_id)
    if n.status == Notification.SENT:
        return
    subject, html, text = render(n.template_code, n.payload)
    try:
        response = get_sender().send(to=n.to_emails, cc=n.cc_emails, subject=subject, html=html, text=text)
    except Exception as exc:
        n.status = Notification.FAILED
        n.error = repr(exc)[:2000]
        n.save(update_fields=["status", "error", "updated_at"])
        log.warning("email send failed", exc_info=True)
        if self.request.is_eager:
            return
        raise self.retry(exc=exc, countdown=30 * (2**self.request.retries))
    n.status = Notification.SENT
    n.sent_at = timezone.now()
    n.response = response[:2000]
    n.error = ""
    n.save(update_fields=["status", "sent_at", "response", "error", "updated_at"])


@shared_task
def daily_digest():
    """Pending count and oldest items for approvers and AP."""
    from apps.invoices.services import pending_digest

    for user, items in pending_digest():
        from .services import notify

        notify("daily_digest", to=[user], payload={"name": user.name, "count": len(items), "items": items[:10]}, link="/console/")
