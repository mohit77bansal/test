# Solfin Vendor Portal

Vendor onboarding (KYC), invoice upload with automatic parsing, maker-checker approvals
(Finance pre-check → L1 → L2 → L3), payment recording, email notifications, reports and an audit log.
One Django 5 codebase: server-rendered templates + HTMX, PostgreSQL 16 with row-level security,
S3 for files, Celery + Redis for jobs.

- Spec: [`docs/vendor-portal-spec.pdf`](docs/vendor-portal-spec.pdf)
- Architecture (Stage 1): [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Permission matrix (generated): [`docs/PERMISSIONS.md`](docs/PERMISSIONS.md)
- Operations: [`docs/RUNBOOK.md`](docs/RUNBOOK.md)

## Layout

```
config/            settings (base/local/test/uat/prod), urls, celery, wsgi
apps/core          base model, RLS helpers + middleware, permissions.py, crypto, seed
apps/accounts      custom User, roles, login + email OTP, password reset, user admin
apps/vendors       vendors, GSTINs, bank accounts, KYC documents, onboarding + review
apps/invoices      invoices, approval matrix + workflow (services.py), queries, payments
apps/parsing       files/S3, virus scan, parse_document() with fake / textract engines
apps/notify        email templates, EmailSender (solfin_api / console), bell
apps/reports       reports, CSV/Excel export of every list
apps/audit         append-only audit log
templates/         vendor/, console/, emails/, partials/ (HTMX fragments)
tests/unit         pytest + pytest-django
tests/e2e          Playwright (pytest-playwright)
docker/            Dockerfile, docker-compose.yml, docker-compose.test.yml, nginx, backup
```

## Local development (no Docker)

Needs Python 3.12+, PostgreSQL 16 and Redis.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

# Postgres: the app must connect as a NON-superuser so row-level security applies
psql -U postgres -c "CREATE ROLE vp_app LOGIN PASSWORD 'vp_app' CREATEDB"
psql -U postgres -c "CREATE DATABASE vendor_portal OWNER vp_app"
psql -U postgres -d vendor_portal -c "CREATE SCHEMA vp AUTHORIZATION vp_app"
psql -U postgres -d template1 -c "CREATE SCHEMA IF NOT EXISTS vp; GRANT ALL ON SCHEMA vp TO vp_app; GRANT ALL ON SCHEMA public TO vp_app"

python manage.py migrate
python manage.py seed --demo        # roles, checklist, templates, CC-OPS, matrix + demo users/vendors
python manage.py runserver          # http://localhost:8000
```

Local settings use the fake parser, console email (OTP codes print in the terminal) and eager Celery.
Demo logins (password `Passw0rd!234`): `vm@test.local`, `vm2@`, `l1@`, `l2@`, `l3@`, `finance@`,
`admin@`, `auditor@` (all `@test.local`); vendors `vendora@test.local`, `vendorb@test.local`.

Rebuild CSS after template changes: `tailwindcss -c tailwind.config.js -i static/src/input.css -o static/css/app.css --minify`
(standalone CLI; the Docker build does this automatically).

## Tests

```bash
pytest tests/unit -n auto                     # unit + request-level tests
pytest tests/e2e -n auto                      # Playwright end-to-end
pytest tests/e2e -m smoke                     # post-deploy smoke
docker compose -f docker/docker-compose.test.yml run --rm tests   # full offline stack (MinIO, fake parser)
```

Tests use `PARSER_ENGINE=fake` (a `FAKE_PARSE:{json}:END` marker in an uploaded file controls the
parsed fields), the locmem email backend (assert on `mail.outbox`) and never call Textract, the Solfin
email API or real AWS. If Playwright's bundled browser is missing, set `PLAYWRIGHT_CHROMIUM_EXECUTABLE`.

## Deploy

See [`docs/RUNBOOK.md`](docs/RUNBOOK.md). In short: copy `.env.example` to `.env`, then

```bash
docker compose --env-file .env -f docker/docker-compose.yml pull
docker compose --env-file .env -f docker/docker-compose.yml run --rm app python manage.py migrate
docker compose --env-file .env -f docker/docker-compose.yml run --rm app python manage.py collectstatic --noinput
docker compose --env-file .env -f docker/docker-compose.yml up -d
```

Post-deploy smoke against the live URL (dedicated smoke accounts with `mfa_enabled` off):

```bash
SMOKE_BASE_URL=https://vendor-uat.solfin.co.in SMOKE_VENDOR_EMAIL=... SMOKE_FINANCE_EMAIL=... \
SMOKE_L1_EMAIL=... SMOKE_PASSWORD=... pytest tests/smoke -p no:django
```

Try the whole stack locally (self-signed TLS on https://localhost:8443, fake parser, console email):

```bash
cp .env.example .env    # set STORAGE_BACKEND=local, PARSER_ENGINE=fake, VIRUS_SCAN_ENGINE=none, EMAIL_SENDER=console, a FIELD_ENCRYPTION_KEY
mkdir -p docker/nginx/certs && openssl req -x509 -nodes -newkey rsa:2048 -days 30 -subj /CN=localhost \
  -keyout docker/nginx/certs/privkey.pem -out docker/nginx/certs/fullchain.pem
export HTTP_PORT=8080 HTTPS_PORT=8443
docker compose --env-file .env -f docker/docker-compose.yml up -d postgres redis
docker compose --env-file .env -f docker/docker-compose.yml run --rm app python manage.py migrate
docker compose --env-file .env -f docker/docker-compose.yml run --rm app python manage.py seed --demo
docker compose --env-file .env -f docker/docker-compose.yml up -d app worker beat nginx
```
