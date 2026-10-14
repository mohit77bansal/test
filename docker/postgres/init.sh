#!/bin/sh
# Creates the non-superuser application role (so Postgres RLS applies) and schema `vp`.
# The schema is also created in template1 so test databases created by the app role inherit it.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE ${APP_DB_USER} LOGIN PASSWORD '${APP_DB_PASSWORD}' CREATEDB NOSUPERUSER NOBYPASSRLS;
ALTER DATABASE ${POSTGRES_DB} OWNER TO ${APP_DB_USER};
CREATE SCHEMA IF NOT EXISTS vp AUTHORIZATION ${APP_DB_USER};
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname template1 <<SQL
CREATE SCHEMA IF NOT EXISTS vp;
GRANT ALL ON SCHEMA vp TO ${APP_DB_USER};
GRANT ALL ON SCHEMA public TO ${APP_DB_USER};
SQL
