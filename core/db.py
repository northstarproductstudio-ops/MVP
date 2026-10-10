"""Database access. The only module that opens Postgres connections.

* API: transaction-mode pooler (port 6543), AsyncConnectionPool, prepared statements off,
  tenancy via ``tenant_transaction(company_id)`` which runs ``SET LOCAL app.company_id``.
* Worker: session-mode pooler (port 5432), sync ConnectionPool of 2 (claim/heartbeat +
  step writes); tenancy enforced in code with ``company_id`` from the run row.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any, Literal

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool

from core.config import Settings
from core.logging import get_logger

log = get_logger(__name__)

Mode = Literal["api", "worker"]

_pools: dict[str, Any] = {}

API_STATEMENT_TIMEOUT_MS = 30_000


def _configure(conn: psycopg.Connection) -> None:
    conn.prepare_threshold = None  # pooler-safe: never prepare


async def _aconfigure(conn: psycopg.AsyncConnection) -> None:
    conn.prepare_threshold = None


def get_pool(mode: Mode, settings: Settings) -> Any:
    """Return the process-wide pool for ``mode`` (created lazily)."""
    if mode in _pools:
        return _pools[mode]
    if mode == "api":
        assert settings.database_url_pooled, "DATABASE_URL_POOLED missing"
        pool = AsyncConnectionPool(
            settings.database_url_pooled.get_secret_value(),
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            kwargs={"prepare_threshold": None, "row_factory": dict_row, "autocommit": False},
            configure=_aconfigure,
            open=False,
        )
    else:
        assert settings.database_url_session, "DATABASE_URL_SESSION missing"
        pool = ConnectionPool(
            settings.database_url_session.get_secret_value(),
            min_size=2,
            max_size=2,
            kwargs={"prepare_threshold": None, "row_factory": dict_row, "autocommit": False},
            configure=_configure,
            open=False,
        )
    _pools[mode] = pool
    return pool


async def close_pools() -> None:
    for mode, pool in list(_pools.items()):
        if isinstance(pool, AsyncConnectionPool):
            await pool.close()
        else:
            pool.close()
        _pools.pop(mode, None)


@contextlib.asynccontextmanager
async def tenant_transaction(
    pool: AsyncConnectionPool, company_id: str, timeout_ms: int = API_STATEMENT_TIMEOUT_MS
) -> AsyncIterator[psycopg.AsyncCursor[Any]]:
    """One API transaction with RLS tenancy set. Safe through the transaction pooler."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
            await cur.execute("SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),))
            yield cur


@contextlib.asynccontextmanager
async def system_transaction(pool: AsyncConnectionPool) -> AsyncIterator[psycopg.AsyncCursor[Any]]:
    """Transaction without tenancy, for job endpoints (sweep) only."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute("SELECT set_config('app.system', 'on', true)")
            yield cur


@contextlib.contextmanager
def worker_transaction(conn: psycopg.Connection) -> Iterator[psycopg.Cursor[Any]]:
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            yield cur


def connect_with_backoff(pool: ConnectionPool, max_wait: float = 30.0) -> None:
    """Open the worker pool, retrying 1, 2, 4 … up to 30 s until the DB answers."""
    delay = 1.0
    while True:
        try:
            if pool.closed:
                pool.open(wait=True, timeout=10)
            with pool.connection(timeout=10) as conn:
                conn.execute("SELECT 1")
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("database unavailable, retrying in %.0fs: %s", delay, type(exc).__name__)
            time.sleep(delay)
            delay = min(delay * 2, max_wait)


def ident(name: str, allowed: Sequence[str]) -> sql.Identifier:
    """Allow-listed identifier for dynamic filters. Never format user input into SQL."""
    if name not in allowed:
        raise ValueError(f"column not allowed: {name}")
    return sql.Identifier(name)


def build_filters(filters: dict[str, Any], allowed: Sequence[str]) -> tuple[sql.Composed, list[Any]]:
    """``col = %s AND …`` from an allow-list; returns (sql, params)."""
    parts: list[sql.Composable] = []
    params: list[Any] = []
    for col, value in filters.items():
        if value is None:
            continue
        parts.append(sql.SQL("{} = %s").format(ident(col, allowed)))
        params.append(value)
    if not parts:
        return sql.Composed([sql.SQL("TRUE")]), params
    return sql.Composed([sql.SQL(" AND ").join(parts)]), params


def bulk_insert(cur: psycopg.Cursor[Any], table: str, columns: list[str], rows: list[Sequence[Any]]) -> int:
    """COPY rows into ``schema.table`` (identifiers composed, never formatted)."""
    if not rows:
        return 0
    schema, name = table.split(".")
    stmt = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
        sql.Identifier(schema), sql.Identifier(name), sql.SQL(", ").join(map(sql.Identifier, columns))
    )
    with cur.copy(stmt) as copy:
        for r in rows:
            copy.write_row(r)
    return len(rows)
