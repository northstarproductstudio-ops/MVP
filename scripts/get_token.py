"""Sign in to Supabase Auth with email + password and print the access token
(for curl / smoke tests against a deployed API).

    python scripts/get_token.py you@company.com
    (prompts for the password; needs SUPABASE_URL and SUPABASE_ANON_KEY in .env or the environment)
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.migrate import _load_env  # noqa: E402


def main() -> int:
    _load_env()
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    email = sys.argv[1]
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/auth/v1/token?grant_type=password"
    anon = os.environ.get("SUPABASE_ANON_KEY") or os.environ["VITE_SUPABASE_ANON_KEY"]
    r = httpx.post(url, headers={"apikey": anon}, json={"email": email, "password": getpass.getpass()}, timeout=30)
    if r.status_code != 200:
        print(f"sign-in failed: {r.status_code} {r.text[:200]}", file=sys.stderr)
        return 1
    tok = r.json()
    company = (tok.get("user", {}).get("app_metadata") or {}).get("company_id")
    print(tok["access_token"])
    print(f"# company_id in token: {company or 'MISSING — run scripts/seed.py company … --user-id'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
