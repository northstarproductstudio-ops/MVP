from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from api import deps


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in {"APP_ENV": "local", "DATABASE_URL_POOLED": "x", "SUPABASE_URL": "http://sb", "SUPABASE_SERVICE_ROLE_KEY": "x",
                 "STORAGE_BUCKET": "b", "OPENROUTER_API_KEY": "x", "LLM_MODEL": "m", "JOB_TOKEN": "t",
                 "SUPABASE_JWT_SECRET": "s" * 40}.items():
        monkeypatch.setenv(k, v)
    deps.settings.cache_clear()
    deps._jwks.cache_clear()
    yield
    deps.settings.cache_clear()


def _claims(company: str | None = "c1") -> dict:
    now = int(time.time())
    c = {"sub": "u1", "aud": "authenticated", "iat": now, "exp": now + 60}
    if company:
        c["app_metadata"] = {"company_id": company}
    return c


def test_hs256_legacy_secret():
    tok = jwt.encode(_claims(), "s" * 40, algorithm="HS256")
    assert deps.current_user(f"Bearer {tok}").company_id == "c1"
    bad = jwt.encode(_claims(), "x" * 40, algorithm="HS256")
    with pytest.raises(HTTPException):
        deps.current_user(f"Bearer {bad}")


def test_es256_via_jwks(monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    tok = jwt.encode(_claims(), key, algorithm="ES256", headers={"kid": "k1"})

    class FakeJwks:
        def get_signing_key_from_jwt(self, _t):
            return type("K", (), {"key": key.public_key()})()

    monkeypatch.setattr(deps, "_jwks", lambda: FakeJwks())
    assert deps.current_user(f"Bearer {tok}").user_id == "u1"


def test_company_claim_required():
    tok = jwt.encode(_claims(None), "s" * 40, algorithm="HS256")
    with pytest.raises(HTTPException) as e:
        deps.current_user(f"Bearer {tok}")
    assert e.value.status_code == 403
