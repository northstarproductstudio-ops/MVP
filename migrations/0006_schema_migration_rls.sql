-- 0006: turn on row-level security for the migration record table.
-- No policy is added on purpose: app_api must never read or change it;
-- only scripts/migrate.py (connecting as postgres) touches this table.
ALTER TABLE meta.schema_migration ENABLE ROW LEVEL SECURITY;
