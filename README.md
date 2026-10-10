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
| `prompts/` | `demand_plan/v1`, `import_agent/v1`, `ask_agent/v1` (versioned) |
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

1. `python scripts/migrate.py` against `DATABASE_URL_SESSION` (with `APP_API_PASSWORD` set the
   first time, to enable the `app_api` login). Services never migrate on startup.
2. `python scripts/seed.py global` (with `GIT_SHA` set) — registers the config and workflow version.
3. `./scripts/build.sh` → `dist/worker`, `dist/api` (each with `core/`, `prompts/`, `config/`,
   `templates/` copied in). `catalyst deploy` the worker first, then the API.
4. Set environment variables per service in the AppSail console (`.env.example` lists them all;
   `app-config.json` holds only non-secret keys).
5. Job Scheduling: `POST /jobs/sweep` every 2 minutes with header `X-Job-Token`; baseline job off.
   (The worker also sweeps every 2 minutes as a fallback.)
6. Check `GET /ready` on the API and `GET /health` on the worker.
7. Web: `cd web && npm run build`, host `web/dist` (e.g. Catalyst web client hosting) with
   `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`; add its origin to `CORS_ORIGINS`.

**Confirm before the first deploy** (from the deployment spec): AppSail outbound access to the
Supabase pooler on 5432/6543; the `app-config.json` keys for your Catalyst CLI version; maximum
request duration for SSE; Job Scheduling calling an AppSail URL with a header; whether a service
can deploy from a parent folder; the pooler client limit and Storage file-size limit on your plan.

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

## Still to do (owners per the plan)

Planner's golden sheet and 30-line quality set; model selection on OpenRouter; back-test
sign-off against "Sep = Aug"; safety-stock days and netting perspective (step 10 is built and off);
the open business decisions listed in the master spec (each is a config or seed change).
