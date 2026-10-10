-- 0005: row-level security and grants for app_api.
-- API traffic connects as app_api (no BYPASSRLS) and sets app.company_id per transaction
-- (core.db.tenant_transaction). The worker connects as a trusted role and filters by
-- company_id in code.

GRANT USAGE ON SCHEMA raw, core, meta TO app_api;

DO $$
DECLARE
  t record;
  shared_tables text[] := ARRAY['core.file_type', 'core.variable', 'meta.config_version',
                                'meta.workflow_version'];
  system_tables text[] := ARRAY['meta.job_queue', 'meta.workflow_run'];
  fqn text;
BEGIN
  FOR t IN
    SELECT n.nspname AS schema, c.relname AS name
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r' AND n.nspname IN ('raw', 'core', 'meta')
      AND c.relname <> 'schema_migration'
  LOOP
    fqn := format('%I.%I', t.schema, t.name);
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', fqn);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', fqn);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO app_api', fqn);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %s', fqn);
    IF (t.schema || '.' || t.name) = ANY(shared_tables) THEN
      EXECUTE format($p$CREATE POLICY tenant_isolation ON %s TO app_api
        USING (company_id IS NULL OR company_id = meta.current_company())
        WITH CHECK (company_id = meta.current_company())$p$, fqn);
    ELSIF (t.schema || '.' || t.name) = ANY(system_tables) THEN
      -- The 2-minute sweeper (POST /jobs/sweep, X-Job-Token) runs as system.
      EXECUTE format($p$CREATE POLICY tenant_isolation ON %s TO app_api
        USING (company_id = meta.current_company() OR meta.is_system())
        WITH CHECK (company_id = meta.current_company() OR meta.is_system())$p$, fqn);
    ELSE
      EXECUTE format($p$CREATE POLICY tenant_isolation ON %s TO app_api
        USING (company_id = meta.current_company())
        WITH CHECK (company_id = meta.current_company())$p$, fqn);
    END IF;
  END LOOP;
END$$;

GRANT SELECT ON meta.schema_migration TO app_api;
GRANT EXECUTE ON FUNCTION meta.current_company() TO app_api;
GRANT EXECUTE ON FUNCTION meta.is_system() TO app_api;
