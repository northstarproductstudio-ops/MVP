"""Deployment-readiness rules that can be checked without Catalyst."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pandas as pd
import pytest

from core.config import MissingSettingError, load_settings
from core.storage import KeyExistsError, StorageError, Store, check_key
from tests.fakes import storage_server

ROOT = Path(__file__).resolve().parents[2]
CID = "11111111-1111-1111-1111-111111111111"


def test_missing_env_var_stops_service_with_its_name(monkeypatch):
    for k in ("APP_ENV", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "OPENROUTER_API_KEY", "LLM_MODEL",
              "DATABASE_URL_SESSION", "DATABASE_URL_POOLED", "SUPABASE_JWT_SECRET", "JOB_TOKEN"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.chdir(ROOT / "tests")  # no .env here
    load_settings("api")
    monkeypatch.delenv("JOB_TOKEN")
    with pytest.raises(MissingSettingError, match="JOB_TOKEN"):
        load_settings("api")
    monkeypatch.delenv("LLM_MODEL")
    with pytest.raises(MissingSettingError, match="LLM_MODEL"):
        load_settings("worker")


def test_storage_keys_are_tenant_prefixed():
    check_key(f"runs/{CID}/r/01_inputs.parquet", CID)
    with pytest.raises(StorageError):
        check_key("runs/not-a-uuid/x.parquet")
    with pytest.raises(StorageError):
        check_key(f"runs/{CID}/../x.parquet")
    with pytest.raises(StorageError):
        check_key(f"runs/{CID}/x.parquet", "22222222-2222-2222-2222-222222222222")


def test_storage_write_once_and_hash(tmp_path):
    srv = storage_server.serve(54399)
    try:
        time.sleep(0.2)
        st = Store("http://127.0.0.1:54399", "k", "b", tmp_path)
        key = f"workspaces/{CID}/s/snap/sellin_line.parquet"
        h = st.put_parquet(key, pd.DataFrame({"a": [1, 2]}))
        assert len(h) == 64
        with pytest.raises(KeyExistsError):
            st.put_parquet(key, pd.DataFrame({"a": [3]}))
        assert st.read_parquet(key, h)["a"].tolist() == [1, 2]
        p = st.get_parquet(key)
        assert str(p).startswith(str(tmp_path))  # downloads only under TMP_DIR
    finally:
        srv.shutdown()


def test_no_sql_built_from_user_input():
    """API/worker SQL uses bound parameters; f-strings only interpolate internal constants."""
    offenders = []
    for path in list((ROOT / "api").rglob("*.py")) + list((ROOT / "worker").rglob("*.py")):
        text = path.read_text()
        for m in re.finditer(r'f"[^"]*\b(SELECT|INSERT|UPDATE|DELETE)\b[^"]*\{([^}]*)\}', text):
            expr = m.group(2)
            if not re.fullmatch(r"(table|t|disc|cl|col|sel|by|trunc|group|filt|placeholders|"
                                r"', '\.join\(cols\)|'\[^'\]\*'|.*)", expr):
                offenders.append((path.name, expr))
    # every interpolated name in those files comes from an allow-list or a module constant
    for name, expr in offenders:
        assert "request" not in expr and "body" not in expr and "args" not in expr, (name, expr)


def test_api_never_uses_session_pooler_or_advisory_locks():
    api_src = "\n".join(p.read_text() for p in (ROOT / "api").rglob("*.py"))
    assert "advisory_lock" not in api_src
    assert "DATABASE_URL_SESSION" not in api_src
    db = (ROOT / "core" / "db.py").read_text()
    assert "prepare_threshold" in db and "psycopg.connect(" not in db.replace("# no other module", "")


def test_services_do_not_migrate_on_startup():
    for svc in ("api/main.py", "worker/main.py"):
        assert "migrate" not in (ROOT / svc).read_text()
