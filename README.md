# Demand Planning MVP

Decision-intelligence app for an FMCG brand selling on Indian quick-commerce and e-commerce
platforms. A planner imports any mix of source files, confirms how their fields are stored,
asks questions in chat, asks for a monthly demand plan, reads its reasoning, edits it, and
exports both reports — with every number traceable to stored factors.

Built from **Demand Planning MVP — Master Build Spec** (behaviour) and the
**Deployment-Readiness Spec** (runtime and hosting). Where they differ, the master spec wins on
behaviour and the deployment spec on runtime.

```
Web (React) ──► fmcg-api (FastAPI) ──► OpenRouter (Ask agent, import agent)
                   │  inserts run + job
                   ▼
            Supabase Postgres  ◄── claims jobs, writes results ── fmcg-worker (steps 1–11, DuckDB)
            raw / core / meta, RLS                                    │  planning agent (step 8)
                   ▲                                                  ▼
            Catalyst Job Scheduling (sweep every 2 min)        Supabase Storage (Parquet)
```

## Repository layout

| Path | Owns |
|---|---|
| `core/` | config (env), `plan_config` (business YAML), `db`, `storage`, `duck`, `logging`, `llm` (OpenRouter), `models/` |
| `core/domain/` | `variables`, `file_types` (detection), `adapters/` (one per file type), `entity_resolution` (crosswalk, customer and city maps), `snapshot`, `known_queries`, `fenced` (read-only lookups), `edits`, `reports` (formatRef export), `plan_math`, `agent_checks` (step-9 validator), `backtest` |
| `api/` | FastAPI app: sessions, imports + mapping, crosswalk, Ask agent (SSE), runs, exports, jobs |
| `worker/` | claim loop, heartbeat, runner, executor (runner contract), `steps/01_validate_inputs.py … 11_build_report.py`, import jobs |
| `web/` | front end for the Figma screens (Import → Map fields → Ask, run card, report card, report views) |
| `migrations/` | numbered SQL: schemas, tables, indexes, `app_api` role, RLS policies |
| `prompts/` | `demand_plan/v2` (category batches), `import_agent/v2`, `ask_agent/v2` (versioned; v1 kept for replay) |
| `config/` | `demand_plan.yaml` (all thresholds), `seed/` (variables, file types, city aliases, customer rules) |
| `templates/formatRef.xlsx` | export template |
| `scripts/` | `migrate.py`, `seed.py`, `build.sh`, `plan_offline.py`, `dev_token.py`, `smoke_e2e.py` |
| `tests/` | unit, golden (pipeline invariants, determinism, hand-checked factors), integration (Postgres RLS, queue), fakes |

## The pipeline (demand_plan v1)

Plan grain is SKU × city × platform. Eleven steps, shown to the user as seven sections:

| Section (MVP-12) | Steps |
|---|---|
| Reading imported data | 1 validate_inputs |
| Cleaning demand data | 2 build_history |
| Correcting for availability | 3 correct_oos (OSA signals; no sell-in correction in v1) |
| Classifying demand patterns | 4 classify_skus (ABC, Syntetos-Boylan) |
| Fixing accuracy baseline | 5 base_forecast (weighted sell-in, Croston-SBA for intermittent, city split) |
| Generating the forecast | 6 derive_factors, 7 build_evidence_pack, 8 agent_plan |
| Building the plan | 9 validate_agent_output, 10 net_and_round, 11 build_report |

Every threshold is a key in `config/demand_plan.yaml`; runs pin a `meta.config_version`. Steps
are deterministic; only steps 8 (planning agent) and 11 (overview text, with template fallback)
may call the model. Every LLM call is written to `meta.agent_call`; a finished run never changes —
edits create report versions.

## Quick start (local)

Requirements: Python 3.12+, Node 22, Postgres 16 (or `docker compose up -d`), a Supabase
project for Storage and Auth (a dev project or `supabase start`).

```bash
cp .env.example .env            # fill in values (see comments)
pip install -r requirements-dev.txt
python scripts/migrate.py       # applies migrations/ to DATABASE_URL_SESSION
python scripts/seed.py global   # file types, variables, config + workflow versions
python scripts/seed.py company "The Select Aisle" --user-id <supabase-user-uuid>

# two terminals
X_ZOHO_CATALYST_LISTEN_PORT=8000 SERVICE_NAME=api python -m uvicorn api.main:app --port 8000
X_ZOHO_CATALYST_LISTEN_PORT=8001 SERVICE_NAME=worker python -u -m worker.main

cd web && cp .env.example .env && npm install && npm run dev   # http://localhost:5173
```

For local testing without Supabase Auth, `python scripts/dev_token.py <company_id>` mints a
token; put it in `web/.env` as `VITE_DEV_TOKEN`.

### November plan without the app (Python pipeline only)

```bash
python scripts/plan_offline.py --data ./sample-data --target 2026-11 --out ./out
```

Writes `plan.xlsx` (formatRef layout), `reasoning.xlsx`, `crosswalk_draft.xlsx` and
`run_summary.json`. The planning agent is off unless `--agent` is passed. If SKUs with sell-in
have crosswalk rows awaiting review, the run stops and lists them (the step-1 rule): review
`crosswalk_draft.xlsx` (set `confirm`), then rerun with `--crosswalk crosswalk_draft.xlsx`, or use
`--no-crosswalk-gate` to plan without those platform factors. `--platforms blinkit,zepto` limits
the plan to some platforms.

## Tests

```bash
pytest -q                                   # unit + golden; Postgres tests skip without:
DATABASE_URL_TEST=postgresql://postgres@localhost:5432/fmcg_test pytest -q
python scripts/smoke_e2e.py --token $TOKEN --data ./sample-data   # full flow against running services
```

Covered: adapter fixes on synthetic copies of every source type, detection and filename
conflicts, crosswalk rules, validator catches every injected bad answer, bridge sums to DP,
DP ≥ 0, city shares sum to 1, determinism (identical pack hashes and fingerprints), hand-checked
trend and base arithmetic, known queries and fenced lookups, edits and versions, formatRef export,
env fail-fast, tenant-prefixed write-once storage, RLS isolation of every table for `app_api`,
two workers never claiming one job, chat lock, sweeper re-queue/fail. The planner's 10-line golden
sheet plugs in as `tests/golden/golden_lines.csv`.

## Deploy (Catalyst AppSail + Supabase)

Step-by-step runbook with checks after every step, the test script and troubleshooting:
**[docs/DEPLOY.md](docs/DEPLOY.md)**. In short: Supabase project → `migrate.py` + `seed.py` →
`build.sh` (vendors Linux/Python 3.12 wheels) → `catalyst appsail:add` + deploy worker then API →
env vars in the console → Slate for the web app → smoke and resilience tests.

AppSail limits the code is built around: requests end at 30 s (SSE streams reconnect; chat waits
20 s for a plan, then follows its progress stream), idle instances are scaled down (the API wakes
the worker via `WORKER_URL`; the worker pings itself while a job runs), and dependencies are
bundled into the build path (Parquet goes through DuckDB, so pyarrow is not shipped).

## Decisions taken while building (where the specs were silent or the data disagreed)

- **Zoho export, second shift.** Besides the org-2 column shift in the spec, about 7,200 org-2
  rows carry the address block one more column right (state in *Shipping City*, city in
  *Shipping Street2*). The adapter repairs this and logs it.
- **Step-1 crosswalk rule.** A run fails when a platform item proposes a match to a SKU with
  sell-in but the row is still awaiting review; platform items with no match at all are excluded
  from factors with a warning. Auto-accept also requires a known, equal pack size.
- **Crosswalk drafts** are re-scored on every import until someone confirms them, so import order
  does not matter.
- **Import jobs run on the worker** (parse → proposal; after save → typed rows, crosswalk draft,
  snapshot). The worker also builds a snapshot when a plan starts if newer datasets are ready.
- **COPY under RLS:** the API inserts with batched `INSERT` (COPY is not allowed for `app_api` on
  RLS tables); the trusted worker uses COPY.
- **Croston-SBA** is implemented natively (same recursion as StatsForecast's `CrostonSBA`) to keep
  the services small.
- **Web framework:** React + Vite + TypeScript.

## Gap fixes (Gap Fix Spec, 11 Oct 2026)

| Gap | What changed | Where |
|---|---|---|
| 1–2 | Primary → fallback model on 429, 5xx, model/provider 400/404 or transport errors; one `agent_call` row per model attempt; no same-model retry on 429 and no rate-limit logic of our own | `core/llm.py`, `LLM_FALLBACK_MODEL` |
| 3–5 | Planning agent runs one call per **category batch** (prompt v2): categories resolved mapped → template → Dcluttr → uncategorised; big categories split into whole-SKU sub-batches; uncategorised SKUs go to `agent.uncategorised_batches` general batches; category factors declared once and expanded per line in step 9; retry resends only failing SKUs; replay by batch key | steps 5, 7, 8, 9; `core/domain/categories.py`, `agent_checks.py` |
| 6 | Python factors stay vectorised and model-free (guardrail; determinism test covers batching) | steps 2–7 |
| 7 | Sales history is the hard gate: `no_sales_file`, `sales_fields_unmapped`, `no_sales_history`, `scope_has_no_history`; SKUs without history are excluded and listed as "Not planned", never planned as 0 | `api/services/runs.py`, step 1, report card |
| 8 | Each file type is essential or enhancer (`file_types.yaml`); the Ask agent gets per-type coverage, known queries return `source_missing`, empty lookups are flagged, suggestions come only from imported types | `core/domain/readiness.py`, `known_queries.py`, `fenced.py`, Ask prompt v2 |
| 8b | One factor → source registry; `core.factor.source_label`, reasoning `sources_used` / `factors_off`, overview sources line | `core/domain/factor_sources.py` |
| 9 | Saved corrections win with zero import-agent calls (test) | `core/domain/importing.py` |
| 10 | `GET /sessions/{id}/readiness` shares one rule with `preconditions()`; "What the plan needs" panel; saving a mapping is never blocked | `api/routers/datasets.py`, `ReadinessPanel.tsx` |
| 11 | `display_name` and `help` on every variable; UI and messages use display names | `config/seed/variables.csv` |
| 12 | Data screen and API: list, add, replace, remove, clear; 409 while a plan runs; finished runs keep their snapshot; nightly worker expiry of removed raw files | `api/routers/datasets.py`, `worker/expiry.py`, `DataScreen.tsx` |

Migration `0006_gap_fixes.sql` adds the new columns. Run it before deploying this code.

## Still to do (owners per the plan)

Planner's golden sheet and 30-line quality set; model selection on OpenRouter; back-test
sign-off against "Sep = Aug"; safety-stock days and netting perspective (step 10 is built and off);
the open business decisions listed in the master spec (each is a config or seed change).
