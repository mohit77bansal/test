from django.db.backends.signals import connection_created
from django.dispatch import receiver


@receiver(connection_created)
def set_session_defaults(sender, connection, **kwargs):
    """Session default: internal context (Celery, management commands).

    VendorIsolationMiddleware overrides these with SET LOCAL for every web request.
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cur:
        cur.execute("SET app.is_internal = 'on'; SET app.vendor_id = ''")
