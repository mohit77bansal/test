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


def forbidden(request, exception=None):
    from django.shortcuts import render

    return render(request, "errors/403.html", status=403)


def not_found(request, exception=None):
    from django.shortcuts import render

    return render(request, "errors/404.html", status=404)
