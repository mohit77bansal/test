from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
PARSER_ENGINE = env("PARSER_ENGINE", default="fake")
VIRUS_SCAN_ENGINE = env("VIRUS_SCAN_ENGINE", default="none")
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="xgimPcMr-DNulTr8SxgUbCxGWZp0pamcJlQ92WX10Vs=")
