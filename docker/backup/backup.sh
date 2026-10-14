#!/bin/sh
# Nightly pg_dump to S3 (30-day retention via the bucket lifecycle rule on the backups/ prefix).
# Restore: see docs/RUNBOOK.md.
set -eu
apk add --no-cache aws-cli >/dev/null 2>&1 || true
while true; do
  now=$(date +%H%M)
  if [ "$now" = "${BACKUP_AT:-0130}" ]; then
    stamp=$(date +%Y%m%d-%H%M%S)
    file="/tmp/vendor_portal-${stamp}.dump"
    PGPASSWORD="$POSTGRES_SUPERUSER_PASSWORD" pg_dump -h "${PGHOST:-postgres}" -U "${POSTGRES_SUPERUSER:-postgres}" -Fc "${POSTGRES_DB:-vendor_portal}" > "$file"
    aws s3 cp "$file" "s3://${BACKUP_BUCKET}/backups/${stamp}.dump" --sse aws:kms --region "${AWS_S3_REGION_NAME:-ap-south-1}"
    rm -f "$file"
    echo "backup ${stamp} uploaded"
    sleep 61
  fi
  sleep 30
done
