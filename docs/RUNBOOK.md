# Runbook

## First install on a Docker host
1. DNS (GoDaddy): A records `vendor.solfin.co.in` / `vendor-uat.solfin.co.in` → host IP.
2. `cp .env.example .env` and fill every value. Secrets stay outside git.
3. TLS: place `fullchain.pem` / `privkey.pem` in `docker/nginx/certs/` (Solfin certificate), or issue with
   Certbot using the `/.well-known/acme-challenge/` location served by nginx.
4. `docker compose --env-file .env -f docker/docker-compose.yml up -d postgres redis` (init script creates the
   non-superuser `vp_app` role and the `vp` schema).
5. `docker compose --env-file .env -f docker/docker-compose.yml run --rm app python manage.py migrate`
6. `docker compose --env-file .env -f docker/docker-compose.yml run --rm app python manage.py seed` (reference data only — no demo users)
7. `docker compose --env-file .env -f docker/docker-compose.yml run --rm app python manage.py createsuperuser` (for `/sysadmin/`), then create internal users in `/console/admin/users/`.
8. `docker compose --env-file .env -f docker/docker-compose.yml up -d`

## Release (promoted by Git tag)
```
docker compose --env-file .env -f docker/docker-compose.yml pull
docker compose --env-file .env -f docker/docker-compose.yml run --rm app python manage.py migrate
docker compose --env-file .env -f docker/docker-compose.yml run --rm app python manage.py collectstatic --noinput
docker compose --env-file .env -f docker/docker-compose.yml up -d
```
Then run the remote `@smoke` suite against the environment (vendor login, upload + parse, Finance pre-check,
L1 approve): `SMOKE_BASE_URL=... SMOKE_VENDOR_EMAIL=... SMOKE_FINANCE_EMAIL=... SMOKE_L1_EMAIL=... SMOKE_PASSWORD=... pytest tests/smoke -p no:django`.
Smoke accounts need `mfa_enabled = False` (set in `/sysadmin/`), an active smoke vendor and a cost centre owned by the smoke L1 user.

Note: Compose reads `.env` from the repo root only because every command passes `--env-file .env`.

## Backups and restore
- `backup` service runs `pg_dump -Fc` nightly at `BACKUP_AT` (default 01:30) to `s3://$BACKUP_BUCKET/backups/`.
  Set a 30-day lifecycle rule on that prefix.
- Restore (test this in UAT before go-live and record the result here):
  ```
  aws s3 cp s3://$BACKUP_BUCKET/backups/<stamp>.dump /tmp/restore.dump
  docker compose --env-file .env -f docker/docker-compose.yml stop app worker beat
  docker compose --env-file .env -f docker/docker-compose.yml exec -T postgres dropdb -U postgres vendor_portal
  docker compose --env-file .env -f docker/docker-compose.yml exec -T postgres createdb -U postgres -O vp_app vendor_portal
  docker compose --env-file .env -f docker/docker-compose.yml exec -T postgres pg_restore -U postgres -d vendor_portal < /tmp/restore.dump
  docker compose --env-file .env -f docker/docker-compose.yml start app worker beat
  ```

## Health and monitoring
- `GET /healthz` → `{"status": "ok", "db": "ok"}`
- Logs are JSON on stdout (`docker compose logs -f app worker`). Sentry when `SENTRY_DSN` is set.
- Failed emails: `notifications` rows with `status=FAILED` (retried 3 times with backoff).
- Failed parses: `parse_jobs` rows with `status=FAILED`; the user fills the form manually.

## Common admin tasks
- Approval limits: `/console/admin/matrix/` (applies to invoices submitted after the change).
- KYC checklist: `/console/admin/documents/`.
- Switch an email off: `/console/admin/templates/` (OTP and password-reset are always on).
- Unlock a user locked out by django-axes: `python manage.py axes_reset_username <email>`.
