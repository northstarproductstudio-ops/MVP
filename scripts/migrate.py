"""Apply numbered SQL migrations in order; record each in meta.schema_migration.

Run from a laptop or CI against DATABASE_URL_SESSION before deploying code that needs
the change. Services never migrate on startup.

    python scripts/migrate.py            # apply pending
    python scripts/migrate.py --status   # list applied / pending
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "migrations"


def _load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def applied(conn: psycopg.Connection) -> set[str]:
    exists = conn.execute("SELECT to_regclass('meta.schema_migration') IS NOT NULL").fetchone()
    if not exists or not exists[0]:
        return set()
    return {r[0] for r in conn.execute("SELECT file_name FROM meta.schema_migration")}


def main() -> int:
    _load_env()
    p = argparse.ArgumentParser()
    p.add_argument("--status", action="store_true")
    p.add_argument("--url", default=os.environ.get("DATABASE_URL_SESSION"))
    args = p.parse_args()
    if not args.url:
        print("DATABASE_URL_SESSION is not set", file=sys.stderr)
        return 2
    files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    with psycopg.connect(args.url, autocommit=False, prepare_threshold=None) as conn:
        done = applied(conn)
        pending = [f for f in files if f.name not in done]
        if args.status:
            for f in files:
                print(("applied " if f.name in done else "pending ") + f.name)
            return 0
        for f in pending:
            print(f"applying {f.name}")
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO meta.schema_migration (file_name) VALUES (%s)", (f.name,))
        pw = os.environ.get("APP_API_PASSWORD")
        if pw:
            with conn.transaction():
                conn.execute(sql.SQL("ALTER ROLE app_api LOGIN PASSWORD {}").format(sql.Literal(pw)))
            print("app_api login enabled")
        print(f"{len(pending)} migration(s) applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
