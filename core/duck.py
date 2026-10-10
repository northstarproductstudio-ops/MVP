"""DuckDB over local Parquet copies (never remote URLs, never live Postgres)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

SNAPSHOT_TABLES = (
    "sellin_line", "po_line", "stock_snapshot", "market_daily", "extra_value",
    "sku_master", "sku_crosswalk", "customer_map", "city_map",
)


def connect(tmp_dir: Path | None = None, memory_limit: str = "1GB") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(f"SET memory_limit='{_safe_limit(memory_limit)}'")
    if tmp_dir is not None:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        con.execute("SET temp_directory=?", [str(tmp_dir / "duck")])
    con.execute("SET threads=2")
    return con


def _safe_limit(limit: str) -> str:
    import re

    if not re.fullmatch(r"\d+(\.\d+)?\s*(KB|MB|GB)", limit.strip(), re.I):
        raise ValueError("invalid memory limit")
    return limit.strip()


def register_frames(con: duckdb.DuckDBPyConnection, frames: dict[str, pd.DataFrame]) -> None:
    """Materialise frames as DuckDB tables (copied, so later frame changes don't leak)."""
    for name, df in frames.items():
        if not name.isidentifier():
            raise ValueError(f"bad table name {name}")
        con.register("_tmp_frame", df)
        con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM _tmp_frame')
        con.unregister("_tmp_frame")


def register_parquet(con: duckdb.DuckDBPyConnection, files: dict[str, Path]) -> None:
    for name, path in files.items():
        if not name.isidentifier():
            raise ValueError(f"bad table name {name}")
        con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM read_parquet(?)', [str(path)])


def lock_down(con: duckdb.DuckDBPyConnection) -> None:
    """Disable file/network access after tables are loaded (fenced lookups)."""
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")
