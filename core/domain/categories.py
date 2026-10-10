"""Plan categories: SKU master ``plan_category``, else the formatRef template grouping."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

UNCATEGORISED = "Other"


@lru_cache(maxsize=4)
def template_layout(path: str) -> tuple[tuple[str, str, str], ...]:
    """(category, sku_code, product name) rows in template order from 'Overall DP'."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=False)
    ws = wb["Overall DP"] if "Overall DP" in wb.sheetnames else wb.worksheets[0]
    out = []
    cat = UNCATEGORISED
    for row in ws.iter_rows(min_row=1, max_col=2, values_only=True):
        a, b = (row + (None, None))[:2]
        if a in (None, "Product") and b in (None, "SKU Code"):
            continue
        if b in (None, "") and a:
            cat = str(a).strip()
            continue
        if b:
            out.append((cat, str(b).strip(), str(a or "").strip()))
    wb.close()
    return tuple(out)


def template_categories(path: Path) -> dict[str, str]:
    return {sku: cat for cat, sku, _ in template_layout(str(path))}


def plan_category_map(skus: list[str], sku_master: pd.DataFrame, template_path: Path) -> dict[str, str]:
    tmpl = template_categories(template_path) if template_path.exists() else {}
    sm = {}
    if sku_master is not None and not sku_master.empty:
        sm = {r.sku_code: r.plan_category for r in sku_master.itertuples() if r.plan_category}
    return {s: sm.get(s) or tmpl.get(s) or UNCATEGORISED for s in skus}
