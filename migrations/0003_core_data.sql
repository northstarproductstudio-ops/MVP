-- 0003: cleaned data (one row per source row after fixes; dataset_id + row_no for lineage)
-- and the company-level entity maps.

CREATE TABLE core.sellin_line (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id     uuid NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  dataset_id     uuid NOT NULL REFERENCES raw.dataset(id) ON DELETE CASCADE,
  row_no         integer NOT NULL,
  so_id          text,
  so_number      text,
  order_date     date NOT NULL,
  status         text,
  customer_name  text,
  platform       text,
  ship_city_raw  text,
  city           text,
  sku_code       text NOT NULL,
  item_name      text,
  qty_ordered    integer,
  qty_invoiced   integer,
  qty_cancelled  integer,
  item_price     numeric(14,2),
  mrp            numeric(14,2),
  reference_no   text,
  is_internal    boolean NOT NULL DEFAULT false,
  branch_id      text,
  line_key       text NOT NULL
);
CREATE INDEX ON core.sellin_line (company_id, sku_code, order_date);
CREATE INDEX ON core.sellin_line (company_id, platform, city);
CREATE INDEX ON core.sellin_line (dataset_id);

CREATE TABLE core.po_line (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id        uuid NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  dataset_id        uuid NOT NULL REFERENCES raw.dataset(id) ON DELETE CASCADE,
  row_no            integer NOT NULL,
  platform          text NOT NULL,
  po_number         text NOT NULL,
  po_date           date,
  status            text CHECK (status IN ('completed', 'expired', 'open', 'cancelled')),
  facility          text,
  city_raw          text,
  city              text,
  platform_item_id  text,
  platform_name     text,
  ean               text,
  sku_code          text,
  qty_ordered       integer,
  qty_received      integer,
  qty_open          integer,
  expected_date     date,
  expiry_date       date,
  unit_cost         numeric(14,2),
  mrp               numeric(14,2)
);
CREATE INDEX ON core.po_line (company_id, sku_code, platform);
CREATE INDEX ON core.po_line (dataset_id);

CREATE TABLE core.stock_snapshot (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id        uuid NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  dataset_id        uuid NOT NULL REFERENCES raw.dataset(id) ON DELETE CASCADE,
  row_no            integer NOT NULL,
  platform          text NOT NULL,
  snapshot_date     date,
  facility          text,
  city_raw          text,
  city              text,
  platform_item_id  text,
  platform_name     text,
  sku_code          text,
  soh_units         integer,
  run_rate          numeric(14,6),
  doh               numeric(14,6)
);
CREATE INDEX ON core.stock_snapshot (dataset_id);

CREATE TABLE core.market_daily (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id        uuid NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  dataset_id        uuid NOT NULL REFERENCES raw.dataset(id) ON DELETE CASCADE,
  row_no            integer NOT NULL,
  platform          text NOT NULL,
  market_date       date NOT NULL,
  variant_id        text NOT NULL,
  platform_item_id  text,
  sku_code          text,
  brand             text,
  is_own_brand      boolean NOT NULL DEFAULT false,
  category          text,
  product_name      text,
  pack_size         text,
  sales_mrp         numeric(14,2),
  sales_sp          numeric(14,2),
  mrp               numeric(14,2),
  sp                numeric(14,2),
  discount_pct      numeric(9,4),
  osa_pct           numeric(9,4),
  cat_share_mrp     numeric(9,6),
  cat_share_sp      numeric(9,6),
  city              text
);
CREATE INDEX ON core.market_daily (company_id, platform, platform_item_id, market_date);
CREATE INDEX ON core.market_daily (dataset_id);

CREATE TABLE core.extra_value (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  dataset_id  uuid NOT NULL REFERENCES raw.dataset(id) ON DELETE CASCADE,
  row_no      integer NOT NULL,
  variable    text NOT NULL,
  value_text  text,
  value_num   numeric,
  value_date  date
);
CREATE INDEX ON core.extra_value (dataset_id);
CREATE INDEX ON core.extra_value (company_id, variable);

CREATE TABLE core.sku_master (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id     uuid NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  dataset_id     uuid REFERENCES raw.dataset(id) ON DELETE SET NULL,
  sku_code       text NOT NULL,
  item_name      text,
  plan_category  text,
  mrp            numeric(14,2),
  ean            text,
  pack_size      text,
  case_size      integer,
  moq            integer,
  UNIQUE (company_id, sku_code)
);

CREATE TABLE core.sku_crosswalk (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id        uuid NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  platform          text NOT NULL,
  platform_item_id  text NOT NULL,
  platform_name     text,
  sku_code          text,
  match_method      text CHECK (match_method IN ('ean', 'auto', 'manual', 'review', 'unmatched')),
  score             numeric(9,6),
  candidates        jsonb,
  confirmed_by      uuid,
  confirmed_at      timestamptz,
  UNIQUE (company_id, platform, platform_item_id)
);

CREATE TABLE core.customer_map (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id     uuid NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  customer_name  text NOT NULL,
  platform       text NOT NULL,
  rule           text,
  UNIQUE (company_id, customer_name)
);

CREATE TABLE core.city_map (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  raw_city    text NOT NULL,
  city        text NOT NULL,
  source      text NOT NULL CHECK (source IN ('seed', 'user')),
  UNIQUE (company_id, raw_city)
);

-- Customer prefix rules are company-level config seeded from customer_rules.csv.
CREATE TABLE core.customer_rule (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id  uuid NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  ord         integer NOT NULL,
  match       text NOT NULL CHECK (match IN ('prefix', 'contains')),
  pattern     text NOT NULL,
  platform    text NOT NULL
);
