-- 0006: Gap Fix Spec (11 Oct 2026). New columns only; no table changes RLS (0005 already
-- covers every table in raw/core/meta, and none are added here).

-- Gaps 3–5: each plan line records the planning-agent batch it was planned in.
ALTER TABLE core.dp_output ADD COLUMN IF NOT EXISTS batch_key text;

-- Gap 8b: every factor names the data it came from.
ALTER TABLE core.factor ADD COLUMN IF NOT EXISTS source_label text;
ALTER TABLE core.factor ADD COLUMN IF NOT EXISTS source_dataset_ids uuid[] NOT NULL DEFAULT '{}';
-- … and every plan line lists the sources it used and the factors that were off.
ALTER TABLE core.dp_output ADD COLUMN IF NOT EXISTS sources_used text;
ALTER TABLE core.dp_output ADD COLUMN IF NOT EXISTS factors_off text;

-- Gap 11: human-readable variable names and one-line help.
ALTER TABLE core.variable ADD COLUMN IF NOT EXISTS display_name text;
ALTER TABLE core.variable ADD COLUMN IF NOT EXISTS help text;

-- Gap 12: data management — remove (soft), replace and data period per dataset.
ALTER TABLE raw.dataset DROP CONSTRAINT IF EXISTS dataset_status_check;
ALTER TABLE raw.dataset ADD CONSTRAINT dataset_status_check
  CHECK (status IN ('uploaded', 'parsing', 'parsed', 'mapped', 'ready', 'failed', 'duplicate', 'removed'));
ALTER TABLE raw.dataset ADD COLUMN IF NOT EXISTS removed_at timestamptz;
ALTER TABLE raw.dataset ADD COLUMN IF NOT EXISTS removed_by uuid;
ALTER TABLE raw.dataset ADD COLUMN IF NOT EXISTS replaces_dataset_id uuid REFERENCES raw.dataset(id);
ALTER TABLE raw.dataset ADD COLUMN IF NOT EXISTS data_from date;
ALTER TABLE raw.dataset ADD COLUMN IF NOT EXISTS data_to date;
ALTER TABLE raw.dataset ADD COLUMN IF NOT EXISTS raw_deleted_at timestamptz;

-- A removed file can be added again later, so it no longer blocks its content hash.
DROP INDEX IF EXISTS raw.dataset_company_sha;
CREATE UNIQUE INDEX dataset_company_sha ON raw.dataset (company_id, content_sha256)
  WHERE status NOT IN ('duplicate', 'failed', 'removed');
CREATE INDEX IF NOT EXISTS dataset_removed ON raw.dataset (status, removed_at) WHERE status = 'removed';

-- Gap 12: data changes leave a short system note in the chat ("Removed Zepto PO Export – Aug-2026").
ALTER TABLE core.chat_message DROP CONSTRAINT IF EXISTS chat_message_role_check;
ALTER TABLE core.chat_message ADD CONSTRAINT chat_message_role_check
  CHECK (role IN ('user', 'assistant', 'tool', 'system'));
