"""Factor → data-source registry (Gap 8b). One place used by step 6, step 9, step 11, the
exports and the Ask agent, so a planner never confuses sales-based numbers with market- or
stock-based ones."""

from __future__ import annotations

from typing import Any

SALES = "Sales history (Zoho sales orders)"
SOURCES: dict[str, str] = {
    "base": SALES,
    "trend": SALES,
    "sell_through": "Market data (Dcluttr) vs sales history",
    "fill_rate": "Platform purchase orders",
    "stock_cover": "Platform stock (Blinkit DOH)",
    "seasonal": SALES,
    "judgment": "Planning agent judgment (no data file)",
    "user_edit": "Planner adjustment",
}
AGENT = "Planning agent judgment (no data file)"
USER = "Planner adjustment"

# the file group each data-backed factor reads (for source_dataset_ids and sources_used)
FACTOR_GROUPS: dict[str, tuple[str, ...]] = {
    "base": ("sellin",), "trend": ("sellin",), "seasonal": ("sellin",),
    "sell_through": ("market", "sellin"), "fill_rate": ("po",), "stock_cover": ("stock",),
}
GROUP_TABLE = {"sellin": "sellin_line", "po": "po_line", "stock": "stock_snapshot", "market": "market_daily"}
GROUP_SHORT = {"sellin": "Sales history", "po": "Platform POs", "stock": "Blinkit stock", "market": "Dcluttr"}

# why a factor is off when its file is missing
OFF_REASON = {"fill_rate": "no PO file", "stock_cover": "no stock file", "sell_through": "no market file",
              "seasonal": "needs 12 months of history"}


def source_label(name: str, origin: str | None = None) -> str:
    if origin == "user" or name.startswith("user"):
        return USER
    if name.startswith("agent:") or origin == "agent":
        return AGENT
    return SOURCES.get(name, SALES if name == "base" else name)


def with_sources(applied: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each applied factor with its ``source`` label."""
    return [{**f, "source": source_label(f["name"])} for f in applied]


def _period(df: Any, col: str) -> str | None:
    import pandas as pd

    if df is None or df.empty or col not in df.columns:
        return None
    s = pd.to_datetime(df[col], errors="coerce").dropna()
    if s.empty:
        return None
    a, b = s.min(), s.max()
    return f"{a:%b %Y}" if (a.year, a.month) == (b.year, b.month) else (
        f"{a:%b}–{b:%b %Y}" if a.year == b.year else f"{a:%b %Y}–{b:%b %Y}")


DATE_COL = {"sellin": "order_date", "po": "po_date", "stock": "snapshot_date", "market": "market_date"}


def group_period(tables: dict[str, Any], group: str, platform: str | None = None) -> str | None:
    df = tables.get(GROUP_TABLE[group])
    if df is not None and platform and "platform" in df.columns:
        df = df[df["platform"] == platform]
    return _period(df, DATE_COL[group])


def dataset_ids(tables: dict[str, Any], groups: tuple[str, ...]) -> list[str]:
    out: set[str] = set()
    for g in groups:
        df = tables.get(GROUP_TABLE[g])
        if df is not None and not df.empty and "dataset_id" in df.columns:
            out.update(str(x) for x in df["dataset_id"].dropna().unique())
    return sorted(out)


def sources_used_text(applied: list[dict[str, Any]], platform: str, tables: dict[str, Any]) -> str:
    """e.g. "Sales history Jul–Sep 2026; Dcluttr Blinkit Sep 2026"."""
    groups: list[str] = ["sellin"]
    agent = False
    for f in applied:
        if f["name"].startswith("agent:") or f["name"] == "judgment":
            agent = True
            continue
        if not f.get("value"):
            continue
        for g in FACTOR_GROUPS.get(f["name"], ()):
            if g not in groups:
                groups.append(g)
    parts = []
    for g in groups:
        plat = platform if g in ("market", "po", "stock") else None
        per = group_period(tables, g, plat)
        name = GROUP_SHORT[g] + (f" {platform.title()}" if plat and g == "market" else "")
        parts.append(f"{name} {per}" if per else name)
    if agent:
        parts.append(AGENT)
    return "; ".join(parts)


def factors_off_text(present: dict[str, bool], extra: list[str] | None = None) -> str | None:
    """e.g. "fill_rate: no PO file; stock_cover: no stock file"."""
    off = []
    for f, g in (("fill_rate", "po"), ("stock_cover", "stock"), ("sell_through", "market")):
        if not present.get(g, False):
            off.append(f"{f}: {OFF_REASON[f]}")
    off += extra or []
    return "; ".join(off) or None
