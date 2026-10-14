from django.contrib import admin

from .models import File, ParseJob

admin.site.register(File)
admin.site.register(ParseJob)
