# One application image for app (gunicorn), worker (Celery) and beat (Celery beat).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Tailwind standalone CLI (no Node runtime in the image).
ARG TAILWIND_VERSION=v3.4.17
RUN curl -sSfL -o /usr/local/bin/tailwindcss \
      https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-x64 \
    && chmod +x /usr/local/bin/tailwindcss

COPY . .
RUN tailwindcss -c tailwind.config.js -i static/src/input.css -o static/css/app.css --minify \
    && DJANGO_SECRET_KEY=build FIELD_ENCRYPTION_KEY=build python manage.py collectstatic --noinput \
    && useradd --system --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fsS http://localhost:8000/healthz || exit 1
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "60", "--access-logfile", "-"]

# Test image: adds pytest + Playwright (Chromium) for docker-compose.test.yml.
FROM base AS test
USER root
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt && playwright install --with-deps chromium
ENV DJANGO_SETTINGS_MODULE=config.settings.test
USER app
