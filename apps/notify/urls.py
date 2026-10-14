from django.urls import path

from . import views

app_name = "notify"
urlpatterns = [
    path("", views.bell, name="bell"),
    path("count/", views.bell_count, name="count"),
    path("read-all/", views.mark_all_read, name="read_all"),
    path("<uuid:pk>/read/", views.mark_read, name="read"),
]
