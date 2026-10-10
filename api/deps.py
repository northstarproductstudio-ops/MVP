"""Shared API dependencies: settings, pool, storage, auth (company_id only from the JWT)."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, Header, HTTPException, status

from core.config import Settings, load_settings
from core.db import get_pool
from core.plan_config import PlanConfig
from core.storage import Store


@lru_cache(maxsize=1)
def settings() -> Settings:
    return load_settings("api")


def pool() -> Any:
    return get_pool("api", settings())


@lru_cache(maxsize=1)
def store() -> Store:
    s = settings()
    return Store(s.supabase_url, s.supabase_service_role_key.get_secret_value(), s.storage_bucket, s.tmp_dir)


@lru_cache(maxsize=1)
def plan_config() -> PlanConfig:
    return PlanConfig.load()


@dataclass(frozen=True)
class User:
    user_id: str
    company_id: str
    email: str | None


def current_user(authorization: Annotated[str | None, Header()] = None) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    secret = settings().supabase_jwt_secret
    if secret is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "auth not configured")
    try:
        claims = jwt.decode(token, secret.get_secret_value(), algorithms=["HS256"], audience="authenticated")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from None
    company = (claims.get("app_metadata") or {}).get("company_id")
    if not company:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "user is not linked to a company")
    return User(user_id=str(claims.get("sub")), company_id=str(company), email=claims.get("email"))


CurrentUser = Annotated[User, Depends(current_user)]


def require_job_token(x_job_token: Annotated[str | None, Header()] = None) -> None:
    expected = settings().job_token
    if expected is None or x_job_token is None or not hmac.compare_digest(
            x_job_token.encode(), expected.get_secret_value().encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid job token")
