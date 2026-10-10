-- 0004: plan results (core) and run machinery (meta).

CREATE TABLE meta.workspace_snapshot (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      uuid NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  session_id      uuid NOT NULL REFERENCES core.chat_session(id) ON DELETE CASCADE,
  dataset_ids     uuid[] NOT NULL,
  as_of           date,
  date_from       date,
  date_to         date,
  table_hashes    jsonb NOT NULL DEFAULT '{}',
  row_counts      jsonb NOT NULL DEFAULT '{}',
  storage_prefix  text NOT NULL
);

CREATE TABLE meta.workflow_version (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id   uuid,
  created_at   timestamptz NOT NULL DEFAULT now(),
  workflow_id  text NOT NULL,
  git_sha      text NOT NULL,
  steps        jsonb NOT NULL
);
CREATE UNIQUE INDEX workflow_version_sha ON meta.workflow_version
  (coalesce(company_id, '00000000-0000-0000-0000-000000000000'::uuid), workflow_id, git_sha);

CREATE TABLE meta.config_version (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid,
  created_at  timestamptz NOT NULL DEFAULT now(),
  parent_id   uuid REFERENCES meta.config_version(id),
  config      jsonb NOT NULL,
  sha256      text NOT NULL,
  created_by  uuid,
  note        text
);
CREATE INDEX ON meta.config_version (company_id, created_at DESC);

CREATE TABLE meta.workflow_run (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id           uuid NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  session_id           uuid NOT NULL REFERENCES core.chat_session(id) ON DELETE CASCADE,
  snapshot_id          uuid REFERENCES meta.workspace_snapshot(id),
  workflow_version_id  uuid REFERENCES meta.workflow_version(id),
  config_version_id    uuid REFERENCES meta.config_version(id),
  prompt_version       text,
  model                text,
  target_month         date NOT NULL,
  scope                jsonb NOT NULL DEFAULT '{}',
  "trigger"            text NOT NULL CHECK ("trigger" IN ('chat', 'retry', 'schedule', 'button', 'backtest')),
  retry_of             uuid REFERENCES meta.workflow_run(id),
  status               text NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  requested_by         uuid,
  started_at           timestamptz,
  finished_at          timestamptz,
  as_of                date,
  dp_total             bigint,
  order_total          bigint,
  warnings             jsonb NOT NULL DEFAULT '[]',
  error                text
);
CREATE INDEX ON meta.workflow_run (session_id, status);

CREATE TABLE meta.run_step (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id          uuid NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  run_id              uuid NOT NULL REFERENCES meta.workflow_run(id) ON DELETE CASCADE,
  step_no             smallint NOT NULL,
  name                text NOT NULL,
  status              text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'running', 'succeeded', 'reused', 'skipped', 'failed')),
  attempt             integer NOT NULL DEFAULT 0,
  fingerprint         text,
  reused_from_run_id  uuid,
  rows_in             integer,
  rows_out            integer,
  artifact_key        jsonb,
  summary             text,
  progress            jsonb,
  warnings            jsonb NOT NULL DEFAULT '[]',
  started_at          timestamptz,
  finished_at         timestamptz,
  error               text,
  UNIQUE (run_id, step_no)
);

CREATE TABLE meta.job_queue (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id        uuid NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  run_id            uuid REFERENCES meta.workflow_run(id) ON DELETE CASCADE,
  session_id        uuid NOT NULL,
  dataset_id        uuid,
  kind              text NOT NULL CHECK (kind IN ('plan', 'import', 'materialize', 'backtest')),
  payload           jsonb NOT NULL DEFAULT '{}',
  status            text NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
  attempt           integer NOT NULL DEFAULT 0,
  locked_by         text,
  heartbeat_at      timestamptz,
  cancel_requested  boolean NOT NULL DEFAULT false,
  error             text
);
CREATE INDEX job_queue_claim ON meta.job_queue (status, created_at);
CREATE INDEX job_queue_sweep ON meta.job_queue (status, heartbeat_at);

CREATE TABLE meta.agent_call (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      uuid NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  run_id          uuid,
  session_id      uuid,
  role            text NOT NULL CHECK (role IN ('import', 'planning', 'ask')),
  sku_code        text,
  attempt         integer NOT NULL DEFAULT 1,
  prompt_version  text,
  model           text,
  request         jsonb,
  response        jsonb,
  valid           boolean,
  failed_checks   jsonb,
  tokens_in       integer,
  tokens_out      integer,
  cost_usd        numeric(12,6),
  latency_ms      integer
);
CREATE INDEX ON meta.agent_call (run_id, role, sku_code, attempt);

CREATE TABLE meta.run_metric (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  run_id      uuid NOT NULL REFERENCES meta.workflow_run(id) ON DELETE CASCADE,
  metric      text NOT NULL,
  class       text,
  value       numeric
);

-- core: plan results

CREATE TABLE core.factor (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id     uuid NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  run_id         uuid NOT NULL REFERENCES meta.workflow_run(id) ON DELETE CASCADE,
  name           text NOT NULL,
  kind           text NOT NULL CHECK (kind IN ('multiplier', 'additive')),
  origin         text NOT NULL CHECK (origin IN ('system', 'agent', 'user')),
  scope          jsonb NOT NULL DEFAULT '{}',
  derived_value  numeric(14,6),
  value          numeric(14,6),
  enabled        boolean NOT NULL DEFAULT true,
  basis          text,
  note           text
);
CREATE INDEX ON core.factor (run_id, name);

CREATE TABLE core.variable_value (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  run_id      uuid NOT NULL REFERENCES meta.workflow_run(id) ON DELETE CASCADE,
  line_id     text NOT NULL,
  name        text NOT NULL,
  value_num   numeric,
  value_text  text
);
CREATE INDEX ON core.variable_value (run_id, line_id);

CREATE TABLE core.dp_output (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id       uuid NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  run_id           uuid NOT NULL REFERENCES meta.workflow_run(id) ON DELETE CASCADE,
  line_id          text NOT NULL,
  sku_code         text NOT NULL,
  item_name        text,
  plan_category    text,
  city             text NOT NULL,
  platform         text NOT NULL,
  abc              text,
  sb_class         text,
  base             numeric(14,4),
  reference_dp     integer,
  final_dp         integer NOT NULL CHECK (final_dp >= 0),
  applied_factors  jsonb NOT NULL DEFAULT '[]',
  bridge           jsonb NOT NULL DEFAULT '[]',
  reason           text,
  confidence       text,
  flag_for_review  boolean NOT NULL DEFAULT false,
  flags            text[] NOT NULL DEFAULT '{}',
  safety_stock     integer,
  order_qty        integer,
  case_size        integer
);
CREATE UNIQUE INDEX dp_output_run_line ON core.dp_output (run_id, line_id);

CREATE TABLE core.report (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  run_id      uuid NOT NULL UNIQUE REFERENCES meta.workflow_run(id) ON DELETE CASCADE,
  payload     jsonb NOT NULL
);

CREATE TABLE core.report_version (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id         uuid NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  run_id             uuid NOT NULL REFERENCES meta.workflow_run(id) ON DELETE CASCADE,
  version            integer NOT NULL,
  parent_version_id  uuid REFERENCES core.report_version(id),
  edit               jsonb,
  note               text,
  overview_text      text,
  created_by         uuid,
  is_current         boolean NOT NULL DEFAULT false,
  UNIQUE (run_id, version)
);
CREATE UNIQUE INDEX report_version_current ON core.report_version (run_id) WHERE is_current;

CREATE TABLE core.dp_line_version (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id         uuid NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  report_version_id  uuid NOT NULL REFERENCES core.report_version(id) ON DELETE CASCADE,
  line_id            text NOT NULL,
  dp_before          integer NOT NULL,
  dp_after           integer NOT NULL CHECK (dp_after >= 0),
  order_qty_after    integer
);
CREATE INDEX ON core.dp_line_version (report_version_id);

CREATE TABLE core.ask_log (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  session_id  uuid NOT NULL,
  question    text NOT NULL,
  path        text CHECK (path IN ('known', 'fenced', 'none')),
  query_name  text,
  sql_text    text,
  row_count   integer,
  latency_ms  integer,
  error       text
);
