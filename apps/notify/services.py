"""notify(): render a template, log Notification rows, enqueue sending after commit."""
from django.conf import settings
from django.db import transaction
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from .models import EmailTemplate, Notification


def _emails(recipients):
    out = []
    for r in recipients or []:
        email = r if isinstance(r, str) else getattr(r, "email", None)
        if email and email not in out:
            out.append(email)
    return out


def render(template_code, payload):
    tpl = EmailTemplate.objects.filter(code=template_code).first()
    subject_src = tpl.subject if tpl else template_code.replace("_", " ").title()
    ctx = {**payload, "SITE_URL": settings.SITE_URL}
    subject = Template(subject_src).render(Context(ctx)).strip()
    html = render_to_string(f"emails/{template_code}.html", ctx)
    text = strip_tags(html)
    return subject, html, text


def notify(template_code, *, to, cc=None, payload=None, link="", in_app=None):
    """Queue an email (to/cc are users or email strings) and optional in-app bell entries.

    `payload` must be JSON-serialisable; every email carries the vendor code, invoice reference
    (when relevant) and a deep link (spec 'Email and notification catalogue').
    """
    payload = dict(payload or {})
    if link:
        payload["link"] = link if link.startswith("http") else settings.SITE_URL.rstrip("/") + link
    tpl = EmailTemplate.objects.filter(code=template_code).first()
    enabled = tpl is None or tpl.enabled or tpl.system
    to_emails, cc_emails = _emails(to), [e for e in _emails(cc) if e not in _emails(to)]
    subject, _, _ = render(template_code, payload)
    created = []
    if to_emails:
        n = Notification.objects.create(
            to_emails=to_emails,
            cc_emails=cc_emails,
            template_code=template_code,
            subject=subject,
            payload=payload,
            channel=Notification.EMAIL,
            status=Notification.QUEUED if enabled else Notification.SKIPPED,
            link=payload.get("link", ""),
        )
        created.append(n)
        if enabled:
            from .tasks import send_notification

            transaction.on_commit(lambda: send_notification.delay(str(n.pk)))
    for user in in_app or []:
        created.append(
            Notification.objects.create(
                user=user,
                template_code=template_code,
                subject=subject,
                payload=payload,
                channel=Notification.IN_APP,
                status=Notification.SENT,
                link=link,
            )
        )
    return created
