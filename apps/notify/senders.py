"""EmailSender seam: `solfin_api` (Solfin custom email API) or `console` (Django mail backend)."""
import json
import urllib.request

from django.conf import settings
from django.core.mail import EmailMultiAlternatives


class EmailSender:
    def send(self, *, to, cc, subject, html, text) -> str:
        """Send one email. Returns a provider response string; raises on failure."""
        raise NotImplementedError


class ConsoleSender(EmailSender):
    """Uses Django's EMAIL_BACKEND: console in dev, locmem in tests (assert on mail.outbox)."""

    def send(self, *, to, cc, subject, html, text):
        msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, to, cc=cc)
        msg.attach_alternative(html, "text/html")
        msg.send()
        return "console:sent"


class SolfinApiSender(EmailSender):
    """Payload format is a placeholder until Solfin shares the API spec (see spec 'Integrations')."""

    def send(self, *, to, cc, subject, html, text):
        if not settings.SOLFIN_EMAIL_API_URL:
            raise RuntimeError("SOLFIN_EMAIL_API_URL is not configured")
        body = json.dumps(
            {"from": settings.DEFAULT_FROM_EMAIL, "to": to, "cc": cc, "subject": subject, "html": html, "text": text}
        ).encode()
        req = urllib.request.Request(
            settings.SOLFIN_EMAIL_API_URL,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings.SOLFIN_EMAIL_API_KEY}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (URL from settings)
            return f"{resp.status}:{resp.read(500).decode(errors='replace')}"


SENDERS = {"console": ConsoleSender, "solfin_api": SolfinApiSender}


def get_sender() -> EmailSender:
    return SENDERS[settings.EMAIL_SENDER]()
