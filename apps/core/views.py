from django.db import connection
from django.http import JsonResponse


def healthz(request):
    try:
        with connection.cursor() as cur:
            cur.execute("select 1")
        db = "ok"
    except Exception:  # pragma: no cover
        db = "error"
    status = 200 if db == "ok" else 503
    return JsonResponse({"status": "ok" if status == 200 else "degraded", "db": db}, status=status)
