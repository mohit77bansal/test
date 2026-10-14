from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def bell(request):
    items = Notification.objects.filter(user=request.user, channel=Notification.IN_APP)[:50]
    return render(request, "notify/list.html", {"items": items})


@login_required
@require_POST
def mark_read(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    n.read_at = n.read_at or timezone.now()
    n.save(update_fields=["read_at", "updated_at"])
    return redirect(n.link or "notify:bell")


@login_required
@require_POST
def mark_all_read(request):
    Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return redirect("notify:bell")


@login_required
def bell_count(request):
    return render(request, "partials/bell.html")
