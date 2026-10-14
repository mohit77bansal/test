"""Base settings shared by every environment. All secrets come from the environment / .env."""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-insecure-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
SITE_URL = env("SITE_URL", default="http://localhost:8000")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "widget_tweaks",
    "axes",
    "apps.core",
    "apps.accounts",
    "apps.audit",
    "apps.notify",
    "apps.parsing",
    "apps.vendors",
    "apps.invoices",
    "apps.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
    "apps.audit.middleware.RequestContextMiddleware",
    "apps.core.middleware.VendorIsolationMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.portal",
            ],
            "builtins": ["apps.core.templatetags.portal_tags"],
        },
    }
]

DATABASES = {
    "default": env.db(
        "DATABASE_URL", default="postgres://vp_app:vp_app@localhost:5432/vendor_portal"
    )
}
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"]["options"] = "-c search_path=vp,public"
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "accounts:login"

# Sessions: secure cookies, idle logout after 30 minutes.
SESSION_COOKIE_AGE = env.int("SESSION_IDLE_SECONDS", default=30 * 60)
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env.bool("SECURE_COOKIES", default=True)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; frame-ancestors 'none'; form-action 'self'"
)

# django-axes: lockout after 5 failed attempts.
AXES_FAILURE_LIMIT = env.int("LOGIN_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = env.float("LOGIN_COOLOFF_HOURS", default=1)
AXES_LOCKOUT_PARAMETERS = ["username"]
AXES_USERNAME_FORM_FIELD = "email"
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = "accounts/locked.html"

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))

# Storage: "s3" (UAT/prod, private bucket, presigned URLs) or "local".
STORAGE_BACKEND = env("STORAGE_BACKEND", default="local")
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", default="vendor-portal")
AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default="ap-south-1")
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default=None)
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = True
AWS_QUERYSTRING_EXPIRE = 300
AWS_S3_FILE_OVERWRITE = False
AWS_S3_OBJECT_PARAMETERS = {"ServerSideEncryption": "aws:kms"} if env.bool("AWS_S3_SSE_KMS", default=True) else {}
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage"
        if STORAGE_BACKEND == "s3"
        else "django.core.files.storage.FileSystemStorage"
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MAX_UPLOAD_BYTES = env.int("MAX_UPLOAD_BYTES", default=10 * 1024 * 1024)
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES + 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
ALLOWED_UPLOAD_TYPES = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}

# Celery
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "daily-digest": {
        "task": "apps.notify.tasks.daily_digest",
        "schedule": 60 * 60 * 24,
    },
}

# Seams (see docs/ARCHITECTURE.md §7)
PARSER_ENGINE = env("PARSER_ENGINE", default="textract")  # textract | fake
LLM_ENGINE = env("LLM_ENGINE", default="none")  # none | bedrock
BEDROCK_MODEL_ID = env("BEDROCK_MODEL_ID", default="")
VIRUS_SCAN_ENGINE = env("VIRUS_SCAN_ENGINE", default="clamd")  # clamd | none
CLAMD_HOST = env("CLAMD_HOST", default="clamav")
CLAMD_PORT = env.int("CLAMD_PORT", default=3310)
EMAIL_SENDER = env("EMAIL_SENDER", default="console")  # solfin_api | console
SOLFIN_EMAIL_API_URL = env("SOLFIN_EMAIL_API_URL", default="")
SOLFIN_EMAIL_API_KEY = env("SOLFIN_EMAIL_API_KEY", default="")
EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Solfin Vendor Portal <no-reply@solfin.co.in>")
AP_INBOX_EMAIL = env("AP_INBOX_EMAIL", default="ap@solfin.co.in")
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")
KMS_KEY_ID = env("KMS_KEY_ID", default="")
OTP_ENABLED = env.bool("OTP_ENABLED", default=True)
OTP_TTL_SECONDS = env.int("OTP_TTL_SECONDS", default=600)
OTP_MAX_ATTEMPTS = env.int("OTP_MAX_ATTEMPTS", default=5)

SENTRY_DSN = env("SENTRY_DSN", default="")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "apps.core.logging.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}

# Lockout is per account (spec); per-IP rate limiting is done by nginx (limit_req on /login/).
SILENCED_SYSTEM_CHECKS = ["axes.W006"]
