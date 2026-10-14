from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "test-secret-key"
ALLOWED_HOSTS = ["*"]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
PARSER_ENGINE = "fake"
OTP_ENABLED = True
LLM_ENGINE = "none"
VIRUS_SCAN_ENGINE = "none"
EMAIL_SENDER = "console"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
# Local filesystem by default; docker-compose.test.yml sets STORAGE_BACKEND=s3 to run against MinIO.
STORAGE_BACKEND = env("STORAGE_BACKEND", default="local")  # noqa: F405
if STORAGE_BACKEND != "s3":
    STORAGES["default"] = {"BACKEND": "django.core.files.storage.FileSystemStorage"}  # noqa: F405
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
FIELD_ENCRYPTION_KEY = "xgimPcMr-DNulTr8SxgUbCxGWZp0pamcJlQ92WX10Vs="
DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405
LOGGING = {"version": 1, "disable_existing_loggers": False}

# Separate test DB per concurrent run (xdist adds its own _gwN suffix).
import os  # noqa: E402

DATABASES["default"]["TEST"] = {"NAME": os.environ.get("TEST_DB_NAME", "test_vendor_portal")}  # noqa: F405
