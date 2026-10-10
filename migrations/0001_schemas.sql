-- 0001: schemas, extensions, roles, migration ledger.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS meta;

-- API role: subject to RLS (no BYPASSRLS). LOGIN + password are set by scripts/migrate.py
-- from APP_API_PASSWORD so no secret lives in SQL.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_api') THEN
    CREATE ROLE app_api NOLOGIN NOBYPASSRLS;
  END IF;
END$$;

CREATE TABLE IF NOT EXISTS meta.schema_migration (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid,
  created_at  timestamptz NOT NULL DEFAULT now(),
  file_name   text NOT NULL UNIQUE,
  applied_at  timestamptz NOT NULL DEFAULT now()
);

-- Helper: tenant id of the current API transaction (NULL when unset).
CREATE OR REPLACE FUNCTION meta.current_company() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.company_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION meta.is_system() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT coalesce(current_setting('app.system', true), '') = 'on'
$$;
