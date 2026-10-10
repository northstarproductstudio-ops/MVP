-- 0002: raw files, workspace (company, chat) and mapping tables.

CREATE TABLE core.company (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id       uuid NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  name             text NOT NULL,
  own_brand_names  text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE core.chat_session (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id           uuid NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  title                text,
  created_by           uuid,
  current_run_id       uuid,
  current_snapshot_id  uuid
);
CREATE INDEX ON core.chat_session (company_id, created_at DESC);

CREATE TABLE core.chat_message (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  session_id  uuid NOT NULL REFERENCES core.chat_session(id) ON DELETE CASCADE,
  role        text NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
  content     text,
  tool_calls  jsonb,
  evidence    jsonb
);
CREATE INDEX ON core.chat_message (session_id, created_at);

CREATE TABLE raw.dataset (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id          uuid NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  session_id          uuid NOT NULL REFERENCES core.chat_session(id) ON DELETE CASCADE,
  file_name           text NOT NULL,
  file_size           bigint,
  content_sha256      text NOT NULL,
  storage_key         text NOT NULL,
  file_type           text,
  type_score          numeric(9,6),
  type_source         text CHECK (type_source IN ('adapter', 'agent', 'user', 'saved')),
  status              text NOT NULL DEFAULT 'uploaded'
                      CHECK (status IN ('uploaded', 'parsing', 'parsed', 'mapped', 'ready', 'failed', 'duplicate')),
  rows_read           integer,
  warnings            jsonb NOT NULL DEFAULT '[]',
  proposal            jsonb,
  progress            jsonb NOT NULL DEFAULT '{}',
  error               text,
  mapping_version_id  uuid
);
CREATE UNIQUE INDEX dataset_company_sha ON raw.dataset (company_id, content_sha256)
  WHERE status <> 'duplicate' AND status <> 'failed';
CREATE INDEX ON raw.dataset (session_id, status);

CREATE TABLE raw.raw_record (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  dataset_id  uuid NOT NULL REFERENCES raw.dataset(id) ON DELETE CASCADE,
  sheet       text NOT NULL,
  row_no      integer NOT NULL,
  data        jsonb NOT NULL
);
CREATE INDEX ON raw.raw_record (dataset_id, sheet, row_no);

-- Seed rows (company_id NULL) are global; companies may add their own (Custom types,
-- agent/user variables).
CREATE TABLE core.file_type (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id   uuid,
  created_at   timestamptz NOT NULL DEFAULT now(),
  key          text NOT NULL,
  name         text NOT NULL,
  signature    jsonb NOT NULL DEFAULT '[]',
  header_rows  int[] NOT NULL DEFAULT '{}',
  is_custom    boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX file_type_key ON core.file_type (coalesce(company_id, '00000000-0000-0000-0000-000000000000'::uuid), key);

CREATE TABLE core.variable (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid,
  created_at  timestamptz NOT NULL DEFAULT now(),
  name        text NOT NULL,
  category    text NOT NULL,
  data_type   text NOT NULL,
  unit        text,
  tier        smallint NOT NULL CHECK (tier IN (1, 2)),
  level       text NOT NULL CHECK (level IN ('hard', 'soft', 'key', 'none')),
  definition  text,
  origin      text NOT NULL CHECK (origin IN ('seed', 'agent', 'user'))
);
CREATE UNIQUE INDEX variable_name ON core.variable (coalesce(company_id, '00000000-0000-0000-0000-000000000000'::uuid), name);

CREATE TABLE core.field_mapping_version (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  file_type   text NOT NULL,
  version     integer NOT NULL,
  created_by  uuid,
  note        text,
  UNIQUE (company_id, file_type, version)
);

CREATE TABLE core.field_mapping (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id     uuid NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  version_id     uuid NOT NULL REFERENCES core.field_mapping_version(id) ON DELETE CASCADE,
  source_sheet   text NOT NULL,
  source_field   text NOT NULL,
  variable       text,
  category       text,
  state          text NOT NULL CHECK (state IN ('recognised', 'proposed', 'new_category',
                   'needs_decision', 'not_stored', 'confirmed', 'custom')),
  confidence     numeric(9,6),
  sample_values  jsonb
);
CREATE INDEX ON core.field_mapping (version_id);
