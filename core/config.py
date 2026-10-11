"""Runtime settings loaded once from the environment (Deployment-Readiness Spec).

Secrets and anything that differs between local and Catalyst come from env.
Business thresholds never live here; they are in config/demand_plan.yaml and
meta.config_version (see core.plan_config).
"""

from __future__ import annotations

import os
import socket
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "production", "test"] = Field(alias="APP_ENV")
    service: str = Field(default="fmcg", alias="SERVICE_NAME")

    database_url_pooled: SecretStr | None = Field(default=None, alias="DATABASE_URL_POOLED")
    database_url_session: SecretStr | None = Field(default=None, alias="DATABASE_URL_SESSION")
    db_pool_min: int = Field(default=1, alias="DB_POOL_MIN")
    db_pool_max: int = Field(default=5, alias="DB_POOL_MAX")

    supabase_url: str = Field(alias="SUPABASE_URL")
    supabase_service_role_key: SecretStr = Field(alias="SUPABASE_SERVICE_ROLE_KEY")
    supabase_jwt_secret: SecretStr | None = Field(default=None, alias="SUPABASE_JWT_SECRET")
    storage_bucket: str = Field(default="fmcg-data", alias="STORAGE_BUCKET")

    openrouter_api_key: SecretStr = Field(alias="OPENROUTER_API_KEY")
    llm_model: str = Field(alias="LLM_MODEL")
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")

    job_token: SecretStr | None = Field(default=None, alias="JOB_TOKEN")
    sentry_dsn: SecretStr | None = Field(default=None, alias="SENTRY_DSN")
    worker_id: str = Field(default_factory=lambda: f"{socket.gethostname()}-{os.getpid()}", alias="WORKER_ID")
    tmp_dir: Path = Field(default=Path("/tmp/fmcg"), alias="TMP_DIR")
    listen_port: int = Field(default=8000, alias="X_ZOHO_CATALYST_LISTEN_PORT")
    cors_origins: str = Field(default="http://localhost:5173", alias="CORS_ORIGINS")
    duckdb_memory_limit: str = Field(default="1GB", alias="DUCKDB_MEMORY_LIMIT")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


# Variables each service must have; checked at startup so a missing one stops the
# service with its name.
REQUIRED = {
    # SUPABASE_JWT_SECRET is only needed for projects still on legacy HS256 token signing
    "api": ["APP_ENV", "DATABASE_URL_POOLED", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
            "STORAGE_BUCKET", "OPENROUTER_API_KEY", "LLM_MODEL", "JOB_TOKEN"],
    "worker": ["APP_ENV", "DATABASE_URL_SESSION", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
               "STORAGE_BUCKET", "OPENROUTER_API_KEY", "LLM_MODEL"],
}


class MissingSettingError(RuntimeError):
    pass


def load_settings(service: str) -> Settings:
    """Load and validate settings for one service; fail fast naming missing vars."""
    try:
        s = Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        names = sorted({str(e["loc"][0]) for e in exc.errors()})
        raise MissingSettingError(f"Missing or invalid environment variables: {', '.join(names)}") from None
    missing = []
    for name in REQUIRED.get(service, []):
        attr = name.lower()
        val = getattr(s, attr, None)
        if val is None or (isinstance(val, SecretStr) and not val.get_secret_value()):
            missing.append(name)
    if missing:
        raise MissingSettingError(f"Missing environment variables for {service}: {', '.join(missing)}")
    s.service = service
    return s


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings(os.environ.get("SERVICE_NAME", "api"))
