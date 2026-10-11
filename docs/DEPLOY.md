# Deploy and test — Demand Planning MVP

A step-by-step runbook from an empty Supabase and Catalyst account to a tested deployment.
Follow the parts in order; each ends with a check, so you know the step worked before
moving on. Plan on 2–3 hours the first time.

**What you'll end up with**

| Piece | Where it runs | URL you'll get |
|---|---|---|
| Database (raw, core, meta + RLS) | Supabase Postgres | pooler connection strings |
| File + Parquet storage | Supabase Storage, private bucket `fmcg-data` | — |
| Sign-in | Supabase Auth | — |
| `fmcg-api` (FastAPI) | Catalyst AppSail | `https://<api>…catalystappsail…` |
| `fmcg-worker` (pipeline) | Catalyst AppSail | `https://<worker>…catalystappsail…` |
| Web app | Catalyst Slate | `https://<app>…` |
| LLM calls | OpenRouter | — |

> Conventions: commands run from the repo root (`MVP/`) unless stated. `<…>` means
> "replace with your value". Keep a private notes file for every value you collect; you'll
> paste them into several places.

---

## Part 0 — Accounts and tools

1. Accounts: **Supabase**, **Zoho Catalyst** (pick the India data centre), **OpenRouter**,
   **GitHub** access to `northstarproductstudio-ops/MVP`.
2. Tools on your laptop:
   - Git, **Python 3.12**, **Node 22** (`node -v`), `pip`
   - Catalyst CLI: `npm install -g zcatalyst-cli`, then `catalyst --version`
   - Optional: `psql` (PostgreSQL client) for the SQL checks below. The Supabase SQL editor works too.
3. Get the code:
   ```bash
   git clone https://github.com/northstarproductstudio-ops/MVP.git && cd MVP
   python3.12 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
   pip install -r requirements-dev.txt
   pytest -q                    # expect: all passed (Postgres tests skipped without DATABASE_URL_TEST)
   ```
   ✅ Check: tests pass on your machine.

---

## Part 1 — Supabase

### 1.1 Create the project
1. supabase.com → **New project**. Region: **South Asia (Mumbai)**, close to Catalyst India.
2. Set a strong **database password** and save it (`DB_PASSWORD`). Avoid `@ : / ?` in it, or
   URL-encode them later.
3. Wait until the project is green.

### 1.2 Collect keys and URLs
**Project Settings → API / API Keys**
- `SUPABASE_URL` = `https://<ref>.supabase.co` (the `<ref>` is your project ref)
- `SUPABASE_SERVICE_ROLE_KEY` = the **service_role** key. If the page shows new-style keys,
  open the **Legacy API keys** tab and copy `service_role` (a long `eyJ…` token). Server-only:
  it must never reach the browser.
- `SUPABASE_ANON_KEY` = the **anon** (or publishable) key; the web app and the token
  helper use it.

**Project Settings → JWT Keys** (or "JWT Settings")
- If the project signs tokens with the **legacy JWT secret (HS256)**, copy it as
  `SUPABASE_JWT_SECRET`.
- If it uses **asymmetric signing keys** (ES256/RS256), leave `SUPABASE_JWT_SECRET` empty:
  the API verifies tokens with the project's public keys (JWKS) automatically.

**Connect (top bar) → Connection string → Pooler**
- **Session pooler** (port **5432**) → `DATABASE_URL_SESSION`:
  `postgresql://postgres.<ref>:<DB_PASSWORD>@aws-0-ap-south-1.pooler.supabase.com:5432/postgres`
- **Transaction pooler** (port **6543**): you'll build the API's string from this host in
  Part 4 (it uses the `app_api` user, not `postgres`).
- Never use the "Direct connection" host (it is IPv6-only).

### 1.3 Storage bucket
**Storage → New bucket** → name `fmcg-data` → **Public: OFF** → Create.
✅ Check: the bucket shows a lock icon (private).

### 1.4 Auth users
1. **Authentication → Sign In / Providers → Email**: enabled. For a pilot you can turn off
   "Confirm email", or confirm users by hand.
2. **Authentication → Users → Add user → Create new user**: the planner's email + password.
   Tick "Auto confirm".
3. Copy the user's **UID** (`PLANNER_USER_ID`).

---

## Part 2 — OpenRouter

1. openrouter.ai → **Keys → Create key** → `OPENROUTER_API_KEY`.
2. Set a **credit limit** on the key: a full plan is about 300 model calls.
3. **Privacy**: the code sends `provider.data_collection = deny`. Also turn off "allow training"
   in your OpenRouter privacy settings.
4. Pick `LLM_MODEL`, a model id that supports **structured outputs / JSON schema** (shown on the
   model's page), and optionally `LLM_FALLBACK_MODEL`, tried once when the primary fails (429,
   5xx, model/provider error). Defaults in `.env.example`: Nemotron 3 Super 120B (free) and
   GLM 4.5 Air (free) — confirm both ids are live with structured-output and tool support.
   Rate limits are OpenRouter's: the code never counts or throttles calls. A plan is now about
   one call per category batch (≈ 30 for the sample data), not one per SKU.

---

## Part 3 — Local configuration

```bash
cp .env.example .env
```
Fill `.env` with the values from Parts 1–2. Also set:
- `APP_API_PASSWORD` — a new strong password for the API's database role (no `@ : / ?`)
- `JOB_TOKEN` — random: `python -c "import secrets;print(secrets.token_urlsafe(32))"`
- `GIT_SHA` — `git rev-parse --short HEAD`
- `DATABASE_URL_POOLED` — the transaction pooler host from 1.2, with the `app_api` user:
  `postgresql://app_api.<ref>:<APP_API_PASSWORD>@aws-0-ap-south-1.pooler.supabase.com:6543/postgres`

---

## Part 4 — Database

```bash
python scripts/migrate.py --status    # lists 0001…0005 as pending
python scripts/migrate.py             # applies them; with APP_API_PASSWORD set, enables the app_api login
python scripts/seed.py global         # file types, variables, config version, workflow version
python scripts/seed.py company "The Select Aisle" --user-id <PLANNER_USER_ID>
```
The last command prints `company_id=…` and writes it into the planner's `app_metadata` in
Supabase Auth. Save it as `COMPANY_ID`.

✅ Checks (Supabase **SQL editor**):
```sql
select file_name from meta.schema_migration order by 1;               -- 5 rows
select count(*) from core.variable where company_id is null;          -- 51
select key from core.file_type order by 1;                            -- 8 types
select rolname, rolbypassrls from pg_roles where rolname in ('app_api','postgres');
-- app_api must be false; postgres true (the worker relies on it)
select raw_app_meta_data->>'company_id' from auth.users where id = '<PLANNER_USER_ID>';  -- = COMPANY_ID
```
If `postgres` shows `rolbypassrls = false`, stop and tell me. The worker would then need its
own `BYPASSRLS` role.

---

## Part 5 — Test locally against the real Supabase (before Catalyst)

This proves the database, storage, auth and OpenRouter values work while everything is
still easy to debug.

### 5.1 Offline November plan (no services needed)
Put the 8 source files in `sample-data/` (git-ignored) and run:
```bash
python scripts/plan_offline.py --data sample-data --target 2026-11 --out out
```
- If it stops with "crosswalk rows await review": open `out/crosswalk_draft.xlsx`, set
  `confirm` to `TRUE` on rows you agree with (fix `proposed_sku` where wrong), save, and rerun
  with `--crosswalk out/crosswalk_draft.xlsx`.
- ✅ Check: `out/plan.xlsx` opens with Overall DP, five city tabs, City shares and Factors;
  `out/reasoning.xlsx` has one row per line; `out/run_summary.json` shows 11 steps.

### 5.2 Run the stack locally
Three terminals (venv active, `.env` loaded automatically):
```bash
# 1 — worker
SERVICE_NAME=worker X_ZOHO_CATALYST_LISTEN_PORT=8001 python -u -m worker.main
# 2 — API
SERVICE_NAME=api X_ZOHO_CATALYST_LISTEN_PORT=8000 python -m uvicorn api.main:app --port 8000
# 3 — web
cd web && cp .env.example .env    # set VITE_API_URL=http://localhost:8000, VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY
npm install && npm run dev        # http://localhost:5173
```
✅ Checks:
```bash
curl localhost:8000/health   # {"status":"ok"}
curl localhost:8000/ready    # {"status":"ready"}  ← database + storage reachable
curl localhost:8001/health   # worker_id, run_id null
```

### 5.3 Scripted end-to-end test
```bash
TOKEN=$(python scripts/get_token.py <planner-email>)     # prompts for the password
python scripts/smoke_e2e.py --api http://localhost:8000 --token "$TOKEN" --data sample-data
```
It creates a chat, uploads the files, saves mappings, asks two questions, requests the
November plan (confirms crosswalk review rows and retries if needed), applies an edit, exports
both reports to `out-smoke/` and undoes the edit.
✅ Check: ends with `after undo [(0, True), (1, False)]` and two exports with status 200.

### 5.4 Manual walk-through in the browser
Do the **functional test script in Part 10.2** at http://localhost:5173.

---

## Part 6 — Catalyst project and AppSail services

### 6.1 Create the project and link the repo
1. console.catalyst.zoho.in → **Create project** → name `demand-planning`.
2. In the repo root:
   ```bash
   catalyst login
   catalyst init        # choose the demand-planning project; select no features for now
   ```
   This creates `catalyst.json` (and `.catalystrc`) in the repo root. Commit `catalyst.json`;
   `.catalystrc` is personal.

### 6.2 Build the deploy folders
```bash
./scripts/build.sh          # Windows: run in Git Bash or WSL
```
This builds `dist/api` and `dist/worker`: shared code plus all Python dependencies, downloaded
for **Linux x86-64, Python 3.12**, which AppSail runs (works from macOS/Windows too).
✅ Check: the output shows two `built … (≈240M / ≈220M)` lines.

### 6.3 Add the two AppSail services
Run twice from the repo root: worker first, then API.
```bash
catalyst appsail:add
```
| Prompt | Worker | API |
|---|---|---|
| Runtime | Catalyst-Managed Runtime | Catalyst-Managed Runtime |
| Sample app? | N | N |
| Source directory | `<repo>/worker` | `<repo>/api` |
| App name | `fmcg-worker` | `fmcg-api` |
| Build path (absolute) | `<repo>/dist/worker` | `<repo>/dist/api` |
| Stack | Python 3.12 | Python 3.12 |

The CLI writes `app-config.json` into `worker/` and `api/`. **Keep the `stack` and `buildPath`
it wrote**, and restore our `command`, `env_variables` and `memory` from git:
```bash
git diff worker/app-config.json api/app-config.json
```
The final files must contain:
- worker `command`: `sh -c 'python3 -u -m worker.main'`
- API `command`: `sh -c 'python3 -u -m uvicorn api.main:app --host 0.0.0.0 --port ${X_ZOHO_CATALYST_LISTEN_PORT}'`
- `"memory": 2048`

### 6.4 Deploy the worker (first)
```bash
cd worker && catalyst deploy appsail && cd ..
```
Note the URL the CLI prints → `WORKER_URL`. The first deploy goes to the **development**
environment.

### 6.5 Configure the worker in the console
Catalyst console → **Serverless → AppSail → fmcg-worker**:
1. **Configuration**: Memory **2048 MB**. Disk: raise from the 256 MB default to **1024 MB**
   (the bundle is about 220 MB, and runs write Parquet scratch under `/tmp/fmcg`).
2. **Environment variables** (development), the secrets:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL_SESSION` | session pooler string (port 5432, user `postgres.<ref>`) |
   | `SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `SUPABASE_SERVICE_ROLE_KEY` | service_role key |
   | `OPENROUTER_API_KEY` | key |
   | `LLM_MODEL` | model id |
   | `LLM_FALLBACK_MODEL` | optional fallback model id |
   | `WORKER_URL` | the worker's own URL from 6.4 |
   | `SENTRY_DSN` | optional |
   | `GIT_SHA` | same as in Part 3 |

   `APP_ENV`, `SERVICE_NAME`, `STORAGE_BUCKET`, `TMP_DIR`, `DUCKDB_MEMORY_LIMIT` come from
   `app-config.json`.
3. Save and **restart / redeploy** the service.

✅ Check: `curl https://<WORKER_URL>/health` returns `{"status":"ok","worker_id":…}`. The
AppSail **Logs** show `worker … ready` as one JSON line.

### 6.6 Deploy and configure the API
```bash
cd api && catalyst deploy appsail && cd ..
```
Note the URL → `API_URL`. Then console → **fmcg-api** → Configuration: memory 2048 MB, disk
1024 MB; Environment variables:

| Variable | Value |
|---|---|
| `DATABASE_URL_POOLED` | transaction pooler string (port 6543, user `app_api.<ref>`) |
| `SUPABASE_URL` | `https://<ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | service_role key |
| `SUPABASE_JWT_SECRET` | only if your project uses the legacy HS256 secret |
| `OPENROUTER_API_KEY`, `LLM_MODEL`, `LLM_FALLBACK_MODEL` | same as the worker |
| `JOB_TOKEN` | from Part 3 |
| `WORKER_URL` | from 6.4 |
| `CORS_ORIGINS` | set in Part 8 once the web URL exists (comma-separated, no `*`) |
| `GIT_SHA`, `SENTRY_DSN` | as above |

Restart the API.
✅ Checks:
```bash
curl https://<API_URL>/health    # {"status":"ok"}
curl https://<API_URL>/ready     # {"status":"ready"}   ← if 503, see Troubleshooting
TOKEN=$(python scripts/get_token.py <planner-email>)
curl -H "Authorization: Bearer $TOKEN" https://<API_URL>/sessions    # [] (empty list, not 401/403)
```

---

## Part 7 — Scheduled jobs (Catalyst Job Scheduling)

The worker already sweeps stale jobs every 2 minutes by itself. These jobs add a backstop
and keep the worker warm (AppSail stops idle instances after a few minutes).

1. Console → **Job Scheduling → Job Pools → Create** → type **Webhook** → `fmcg-jobs`.
2. **Job: keep the worker warm.** Webhook `GET https://<WORKER_URL>/health`; Cron: every
   2 minutes. Pre-defined cron, e.g. `*/2 * * * *`.
3. **Job: sweeper (optional).** Webhook `POST https://<API_URL>/jobs/sweep` with header
   `X-Job-Token: <JOB_TOKEN>`; every 2 minutes. If the webhook form has no custom headers,
   skip this job: the worker's own sweep covers it.
4. Leave the baseline job off. `schedule.baseline_enabled` is false.

✅ Check: after 10 minutes, the job's run history shows successful calls.

---

## Part 8 — Web app on Catalyst Slate

1. Build-time settings (Vite bakes them in). Create `web/.env.production`:
   ```
   VITE_API_URL=https://<API_URL>
   VITE_SUPABASE_URL=https://<ref>.supabase.co
   VITE_SUPABASE_ANON_KEY=<anon key>
   ```
   (Do **not** set `VITE_DEV_TOKEN` here.)
2. From the repo root: `catalyst init slate` → framework **React + Vite** → source **`web`**.
   Keep the defaults `npm install` / `npm run build`; build output is **`dist`**.
3. `catalyst deploy slate` → note the URL → `WEB_URL`.
4. API env var `CORS_ORIGINS=https://<WEB_URL>` → restart the API.
5. Supabase → **Authentication → URL Configuration**: Site URL = `https://<WEB_URL>`.

✅ Check: open `WEB_URL`, sign in as the planner, and land on "Import your sources".
Browser dev tools → Network shows no CORS errors.

---

## Part 9 — Development → production

Everything above lands in Catalyst's **development** environment. When Part 10 passes:
1. Console → **Deploy to production** (project settings).
2. Set every environment variable again for **production** (they are per environment).
3. Production URLs drop the "development" segment. Update `WORKER_URL`, `CORS_ORIGINS`,
   `VITE_API_URL` (rebuild + `catalyst deploy slate --production`) and the cron URLs.
4. Repeat the health checks (6.5, 6.6) and the smoke test (10.1) against production.

---

## Part 10 — Test the deployment

### 10.1 Scripted smoke test (5 minutes)
```bash
TOKEN=$(python scripts/get_token.py <planner-email>)
python scripts/smoke_e2e.py --api https://<API_URL> --token "$TOKEN" --data sample-data
```
Pass = the same ending as 5.3. Note: this creates a "Smoke test" chat in the planner's
account. That's fine for a pilot; use a separate test user if you prefer.

### 10.2 Functional test script (planner, in the browser, about 30 minutes)

| # | Do | Expect |
|---|---|---|
| 1 | Sign in at `WEB_URL` | "Import your sources", 0 of 10 files |
| 2 | Drop the 8 files → **Start process** | Each file: Queued → Reading → Complete; rows read, fields detected, header found |
| 3 | Mapping screen | Each file's type is right (Zoho = ERP sales orders, Swiggy = Instamart PO, etc.). Chips: mapped / unmapped / new category / custom |
| 4 | Change one field's category; add a custom mapping; **Confirm and save** | "Mapping saved": variables stored, categories, not stored. "Fixes applied" lists the Zoho org-2 shift and address fix, Blinkit `-` → blank |
| 5 | Crosswalk notice | Review rows listed; **Confirm all proposals** (or download, fix, upload) |
| 6 | **Go to Ask** | Suggested questions in "about the data"; header shows files, date range, SKUs |
| 7 | Ask "Show Chennai sales by channel for the last 3 months" | Answer with figures; "What it rests on" says *Known query: sales_history* |
| 8 | Ask something unusual, e.g. "Which PO numbers expired on Instamart in August?" | Answer labelled *Direct lookup from imported data, not a pipeline figure* |
| 9 | Ask "Create a demand plan for November" | Workflow card ticks the 7 sections; planning agent progress "N of 77 SKUs"; then the report card (total DP, vs last month, lines, fallback/review) and an overview paragraph |
| 10 | **View plan** / **View reasoning** | Plan by SKU × city; reasoning per line: base, trend, sell-through, fill rate, stock cover, agent factors (e.g. `agent:diwali`), reference, final DP, reason |
| 11 | Ask "increase Cake Mixes by 30%" | Reply states lines changed and units before → after; card shows version 1; reasoning "Your change" filled |
| 12 | **Export plan** and **Export reasoning** | `.xlsx` files open; plan has Overall DP, 5 city tabs, City shares, Factors; numbers match the plan view |
| 13 | Ask "undo" (or **Undo v1**) | Back to version 0 |
| 14 | Reload the page | Chat history, run card and report card are still there |
| 15 | Ask "Create a demand plan for November" again while one is running | Refused: "A plan is already running in this chat" |

### 10.3 Data checks (Supabase SQL editor)
Replace `<COMPANY_ID>`.
```sql
-- Sell-in reconciles to the Zoho file (compare with a pivot of the Zoho export: invoiced+confirmed,
-- QuantityInvoiced + QuantityCancelled, excluding Symbiate customers)
select date_trunc('month', order_date)::date m, sum(coalesce(qty_invoiced,0)+coalesce(qty_cancelled,0)) units
from core.sellin_line where company_id='<COMPANY_ID>' and status in ('invoiced','confirmed') and not is_internal
group by 1 order by 1;

-- Latest run: totals, warnings, steps
select id, status, target_month, dp_total, warnings from meta.workflow_run
where company_id='<COMPANY_ID>' order by created_at desc limit 3;
select step_no, name, status, rows_out, summary from meta.run_step where run_id='<RUN_ID>' order by step_no;

-- Invariants: DP never negative; no partial plans from failed runs
select count(*) from core.dp_output where final_dp < 0;                                -- 0
select count(*) from core.dp_output d join meta.workflow_run r on r.id=d.run_id
where r.status <> 'succeeded';                                                          -- 0

-- LLM usage and cost of the run
select role, count(*), sum(tokens_in) tin, sum(tokens_out) tout, sum(cost_usd) usd
from meta.agent_call where run_id='<RUN_ID>' group by 1;
```

### 10.4 Resilience checks (deployment-readiness checklist)

| Test | How | Expect |
|---|---|---|
| Worker restart mid-run | Start a plan; when "Generating the forecast" is running, **restart fmcg-worker** in the console | Run continues within ~2 min; `run_step` shows earlier steps `reused` or `succeeded`; one final plan, no duplicates |
| Redeploy during a run | `catalyst deploy appsail` for the worker while a run is active | Current step finishes; job resumes after the new instance starts |
| Cancel | Click **Stop the workflow** | Run `cancelled`; no `dp_output` rows for it |
| Model outage | In the **worker** env set `OPENROUTER_API_KEY` to an invalid value, restart, run a plan | Run **succeeds**; lines carry `agent_fallback`; card shows fallback count = lines. Restore the key afterwards |
| Missing env var | Remove `JOB_TOKEN` from the API env and restart | Service fails to start; log names `JOB_TOKEN`. Restore it |
| Tenant isolation | `python scripts/seed.py company "Test Co" --user-id <second test user>`; sign in as that user | Sees no chats, files or plans from The Select Aisle |
| Job endpoints locked | `curl -X POST https://<API_URL>/jobs/sweep` | `401` without the header; `200` with `-H "X-Job-Token: <JOB_TOKEN>"` |
| Logs | AppSail logs during a run | One JSON object per line with `run_id` and `step`; no keys or tokens visible |

### 10.5 Sign-off
- [ ] 10.1 smoke test passes on production
- [ ] 10.2 steps 1–15 pass for the planner
- [ ] 10.3 reconciliation matches the Zoho pivot to the unit; invariants return 0
- [ ] 10.4 all resilience checks pass
- [ ] Planner reviewed flagged lines (market ceiling, stale stock, agent fallback)

---

## Part 11 — Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Service won't start; log says `Missing environment variables: X` | Env var not set for this environment | Add it in AppSail → Environment variables (dev and prod are separate) |
| Instance killed at start | App didn't listen within 10 s | Check the `command` in `app-config.json`; the API must bind `${X_ZOHO_CATALYST_LISTEN_PORT}` |
| `/ready` → 503 "database unreachable" | Wrong pooler string or password; app_api login not enabled | Re-run `scripts/migrate.py` with `APP_API_PASSWORD`; check the user is `app_api.<ref>` and port 6543 |
| `/ready` → 503 "storage unreachable" | Wrong `SUPABASE_URL`/service key, or bucket name | Bucket `fmcg-data` must exist; use the legacy `service_role` key |
| API calls return 401 | Token signing mismatch | Legacy-HS256 projects need `SUPABASE_JWT_SECRET`; asymmetric projects need it empty |
| API calls return 403 "not linked to a company" | User's `app_metadata.company_id` missing | `python scripts/seed.py company … --user-id <uid>` (or set app_metadata in Supabase); sign in again |
| Browser shows CORS errors | `CORS_ORIGINS` missing the web URL | Set the exact origin (scheme + host, no trailing slash); restart the API |
| Files stay "Queued" | Worker asleep or not running | Open `WORKER_URL/health`; check the keep-warm cron (Part 7); check worker logs |
| Import "failed" with a message | File differs from known layouts | The message names the problem. Use **File type** on the mapping screen to correct the type |
| Plan stops: "crosswalk rows await review" | Expected (step-1 rule) | Confirm the listed SKUs on the Saved screen, then ask again or **Retry** |
| Plan stops: "required fields not mapped" | A hard field (e.g. `qty_invoiced`) was left unmapped | Re-map the Zoho file |
| Chat says "still running" | Plan longer than the 20 s chat wait | Normal: the workflow card keeps updating and the report card appears when done |
| Many `agent_fallback` lines | Model output failed validation, or model unreachable | Check `meta.agent_call.failed_checks`; try another `LLM_MODEL` |
| Out-of-memory / killed during a run | Memory too low | Memory 2048 MB; lower `DUCKDB_MEMORY_LIMIT` to `768MB` |

---

## Part 12 — Updating later

1. Pull the new code. If `migrations/` changed, **run `python scripts/migrate.py` first**
   (services never migrate themselves).
2. If `config/demand_plan.yaml` changed: `python scripts/seed.py global` (creates a new
   config version; later runs pin it).
3. `./scripts/build.sh` → deploy the **worker**, then the **API**: `catalyst deploy appsail`
   in `worker/`, then in `api/`.
4. Web changed: `catalyst deploy slate`.
5. Re-run `/health`, `/ready` and the smoke test (10.1).

## Items to confirm with Catalyst support (from the deployment spec)
- Outbound access from AppSail to the Supabase pooler on ports 5432 and 6543 (`/ready` proves it)
- Whether webhook jobs can send a custom header (`X-Job-Token`)
- Whether an idle AppSail instance can be kept running (the worker's keep-alive and the cron
  cover it, but a pinned minimum instance would be cleaner)
- The 30-second request limit: the app keeps every request under it (streams reconnect), so no
  change is needed unless they offer longer
