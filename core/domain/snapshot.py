"""Workspace snapshot: frozen, de-duplicated, entity-resolved tables a run reads.

The snapshot uses all ready datasets of a chat, de-duplicated on each table's natural key
(newest dataset wins), with the company's current maps applied so later crosswalk or
city-alias confirmations take effect without re-importing.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from core.domain import entity_resolution as er
from core.domain.tables import NATURAL_KEYS, TABLE_COLUMNS, conform, empty_frame

DATA_TABLES = ("sellin_line", "po_line", "stock_snapshot", "market_daily", "extra_value")
MAP_TABLES = ("sku_master", "sku_crosswalk", "customer_map", "city_map")


@dataclass
class Snapshot:
    tables: dict[str, pd.DataFrame]
    as_of: pd.Timestamp | None
    date_from: pd.Timestamp | None
    date_to: pd.Timestamp | None
    warnings: list[str] = field(default_factory=list)
    unmatched_cities: list[dict[str, Any]] = field(default_factory=list)

    def hashes(self) -> dict[str, str]:
        return {name: table_hash(df) for name, df in sorted(self.tables.items())}

    def row_counts(self) -> dict[str, int]:
        return {k: int(len(v)) for k, v in self.tables.items()}


def table_hash(df: pd.DataFrame) -> str:
    """Content hash independent of row order."""
    if df.empty:
        return hashlib.sha256(",".join(df.columns).encode()).hexdigest()
    cols = sorted(df.columns)
    h = pd.util.hash_pandas_object(df[cols].astype("string"), index=False)
    return hashlib.sha256(io.BytesIO(h.sort_values().to_numpy().tobytes()).getvalue()).hexdigest()


def dedupe(frames: list[pd.DataFrame], table: str) -> pd.DataFrame:
    """``frames`` oldest → newest dataset; newest wins on the natural key."""
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return empty_frame(table)
    df = pd.concat([conform(f, table) for f in frames], ignore_index=True)
    key = NATURAL_KEYS.get(table)
    if key:
        df = df.drop_duplicates(subset=key, keep="last")
    return df.reset_index(drop=True)


def build_snapshot(
    data: dict[str, list[pd.DataFrame]],
    sku_master: pd.DataFrame | None,
    crosswalk: pd.DataFrame | None,
    customer_rules: list[er.CustomerRule],
    city_aliases: dict[str, str],
    customer_map_saved: pd.DataFrame | None = None,
    unmatched_share: float = 0.01,
    strip_tokens: list[str] | None = None,
    auto_accept: float = 0.9,
    review_min: float = 0.75,
    draft_crosswalk: bool = True,
    tie_margin: float = 0.01,
) -> Snapshot:
    tables = {t: dedupe(data.get(t, []), t) for t in DATA_TABLES}
    sm = conform(sku_master, "sku_master") if sku_master is not None and not sku_master.empty \
        else empty_frame("sku_master")
    warnings: list[str] = []

    # customers → platform
    sellin = tables["sellin_line"]
    cmap = er.build_customer_map(sellin["customer_name"], customer_rules, customer_map_saved)
    pmap = dict(zip(cmap.customer_name, cmap.platform, strict=False))
    sellin["platform"] = sellin["customer_name"].map(pmap).fillna("other")
    sellin["is_internal"] = sellin["platform"].eq("internal")
    # cities
    sellin["city"] = er.city_series(sellin["ship_city_raw"], city_aliases)
    for t in ("po_line", "stock_snapshot"):
        df = tables[t]
        df["city"] = er.city_series(df["city_raw"].fillna(df["facility"]), city_aliases)
    md = tables["market_daily"]
    if md["city"].notna().any():
        md.loc[md["city"].notna(), "city"] = er.city_series(md.loc[md["city"].notna(), "city"], city_aliases)

    # crosswalk (draft new platform IDs, keep confirmed rows) and apply
    cw = conform(crosswalk, "sku_crosswalk") if crosswalk is not None and not crosswalk.empty \
        else empty_frame("sku_crosswalk")
    if draft_crosswalk:
        items = er.platform_items(tables["po_line"], tables["stock_snapshot"], tables["market_daily"])
        refs = er.sku_refs(sellin, sm, strip_tokens or [])
        drafted = er.build_crosswalk(items, refs, cw, strip_tokens or [], auto_accept, review_min, tie_margin)
        if not drafted.empty:
            cw = conform(drafted, "sku_crosswalk").assign(
                proposed_sku=drafted.get("proposed_sku", pd.Series([None] * len(drafted))).values)
    for t in ("po_line", "stock_snapshot", "market_daily"):
        tables[t] = er.apply_crosswalk(tables[t], cw)
    tables["sku_master"] = sm
    tables["sku_crosswalk"] = cw
    tables["customer_map"] = conform(cmap, "customer_map")
    cm = pd.DataFrame(sorted(city_aliases.items()), columns=["raw_city", "city"]).assign(source="seed")
    tables["city_map"] = conform(cm, "city_map")

    unmatched = er.unmatched_cities(sellin, unmatched_share)
    if unmatched:
        warnings.append("Unmatched ship cities above 1% of volume: "
                        + ", ".join(f"{u['raw_city']} ({u['share']:.1%})" for u in unmatched[:8]))
    dates = sellin["order_date"].dropna()
    as_of = dates.max() if len(dates) else None
    for name in tables:
        tables[name] = tables[name][[c for c in TABLE_COLUMNS[name]] +
                                    [c for c in tables[name].columns if c not in TABLE_COLUMNS[name]]]
    return Snapshot(tables=tables, as_of=as_of, date_from=dates.min() if len(dates) else None,
                    date_to=as_of, warnings=warnings, unmatched_cities=unmatched)
