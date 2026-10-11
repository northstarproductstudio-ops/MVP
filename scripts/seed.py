"""Seed global data and create a company.

    python scripts/seed.py global                       # file types, variables, config version
    python scripts/seed.py company "The Select Aisle" [--user-id <supabase user uuid>]

``global`` is idempotent and creates a new config_version only when demand_plan.yaml changed.
``company`` creates the company with its customer rules and city aliases; with --user-id it
sets the user's app_metadata.company_id through the Supabase admin API so their JWT carries it.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import uuid
from pathlib import Path

import httpx
import psycopg
import yaml
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.plan_config import PlanConfig  # noqa: E402
from scripts.migrate import _load_env  # noqa: E402

SEED = ROOT / "config" / "seed"


def seed_global(conn: psycopg.Connection) -> None:
    with conn.transaction():
        for ft in yaml.safe_load((SEED / "file_types.yaml").read_text()):
            conn.execute(
                """INSERT INTO core.file_type (company_id, key, name, signature, header_rows, is_custom)
                   VALUES (NULL,%s,%s,%s,%s,%s)
                   ON CONFLICT (coalesce(company_id, '00000000-0000-0000-0000-000000000000'::uuid), key)
                   DO UPDATE SET name=excluded.name, signature=excluded.signature, header_rows=excluded.header_rows""",
                (ft["key"], ft["name"], Jsonb(ft["signature"]), ft["header_rows"], ft["key"] == "custom"))
        with open(SEED / "variables.csv", encoding="utf-8") as fh:
            for v in csv.DictReader(fh):
                conn.execute(
                    """INSERT INTO core.variable (company_id, name, category, data_type, unit, tier, level, definition,
                       display_name, help, origin) VALUES (NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,'seed')
                       ON CONFLICT (coalesce(company_id, '00000000-0000-0000-0000-000000000000'::uuid), name)
                       DO UPDATE SET category=excluded.category, data_type=excluded.data_type, unit=excluded.unit,
                       tier=excluded.tier, level=excluded.level, definition=excluded.definition,
                       display_name=excluded.display_name, help=excluded.help""",
                    (v["name"], v["category"], v["data_type"], v["unit"] or None, int(v["tier"]), v["level"],
                     v["definition"], v["display_name"], v["help"] or None))
        from worker.steps import steps_manifest

        sha = os.environ.get("GIT_SHA", "dev")
        conn.execute(
            """INSERT INTO meta.workflow_version (company_id, workflow_id, git_sha, steps)
               VALUES (NULL, 'demand_plan', %s, %s)
               ON CONFLICT (coalesce(company_id, '00000000-0000-0000-0000-000000000000'::uuid), workflow_id, git_sha)
               DO UPDATE SET steps = excluded.steps""", (sha, Jsonb(steps_manifest())))
        cfg = PlanConfig.load()
        row = conn.execute("SELECT id, sha256 FROM meta.config_version WHERE company_id IS NULL "
                           "ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row or row[1] != cfg.sha256:
            conn.execute("INSERT INTO meta.config_version (company_id, parent_id, config, sha256, note) "
                         "VALUES (NULL,%s,%s,%s,'seeded from config/demand_plan.yaml')",
                         (row[0] if row else None, Jsonb(cfg.as_dict()), cfg.sha256))
            print("new config version seeded")
    print("global seed done")


def create_company(conn: psycopg.Connection, name: str, brands: list[str]) -> str:
    cid = str(uuid.uuid4())
    with conn.transaction():
        conn.execute("INSERT INTO core.company (id, company_id, name, own_brand_names) VALUES (%s,%s,%s,%s)",
                     (cid, cid, name, brands))
        with open(SEED / "customer_rules.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                conn.execute("INSERT INTO core.customer_rule (company_id, ord, match, pattern, platform) "
                             "VALUES (%s,%s,%s,%s,%s)", (cid, int(r["order"]), r["match"], r["pattern"], r["platform"]))
        with open(SEED / "city_aliases.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                conn.execute("INSERT INTO core.city_map (company_id, raw_city, city, source) VALUES (%s,%s,%s,'seed') "
                             "ON CONFLICT DO NOTHING", (cid, r["raw_city"], r["city"]))
    return cid


def link_user(user_id: str, company_id: str) -> None:
    url = os.environ["SUPABASE_URL"].rstrip("/") + f"/auth/v1/admin/users/{user_id}"
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    r = httpx.put(url, headers={"Authorization": f"Bearer {key}", "apikey": key},
                  json={"app_metadata": {"company_id": company_id}}, timeout=30)
    r.raise_for_status()
    print(f"user {user_id} linked to company {company_id}")


def main() -> int:
    _load_env()
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("global")
    c = sub.add_parser("company")
    c.add_argument("name")
    c.add_argument("--brand", action="append", help="own brand name (repeatable)")
    c.add_argument("--user-id")
    args = p.parse_args()
    url = os.environ.get("DATABASE_URL_SESSION")
    if not url:
        print("DATABASE_URL_SESSION is not set", file=sys.stderr)
        return 2
    with psycopg.connect(url, prepare_threshold=None) as conn:
        if args.cmd == "global":
            seed_global(conn)
        else:
            cid = create_company(conn, args.name, args.brand or PlanConfig.load().get("brand.own_names"))
            print(f"company_id={cid}")
            if args.user_id:
                link_user(args.user_id, cid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
