"""Mint a local development JWT shaped like a Supabase user token (HS256, aud=authenticated,
app_metadata.company_id). Local use only; production tokens come from Supabase Auth.

    python scripts/dev_token.py <company_id> [--user <uuid>] [--hours 12]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

import jwt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.migrate import _load_env  # noqa: E402


def main() -> int:
    _load_env()
    p = argparse.ArgumentParser()
    p.add_argument("company_id")
    p.add_argument("--user", default=str(uuid.uuid4()))
    p.add_argument("--hours", type=float, default=12)
    a = p.parse_args()
    if os.environ.get("APP_ENV") == "production":
        print("refusing to mint tokens in production", file=sys.stderr)
        return 2
    now = int(time.time())
    tok = jwt.encode({"sub": a.user, "aud": "authenticated", "role": "authenticated", "iat": now,
                      "exp": now + int(a.hours * 3600), "email": "dev@example.com",
                      "app_metadata": {"company_id": a.company_id}},
                     os.environ["SUPABASE_JWT_SECRET"], algorithm="HS256")
    print(tok)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
