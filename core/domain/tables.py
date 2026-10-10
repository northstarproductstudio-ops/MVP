"""Column contracts for the cleaned core tables and variable→column maps per file group."""

from __future__ import annotations

import pandas as pd

TABLE_COLUMNS: dict[str, dict[str, str]] = {
    "sellin_line": {
        "dataset_id": "string", "row_no": "Int64", "line_key": "string", "so_id": "string",
        "so_number": "string", "order_date": "date", "status": "string", "customer_name": "string",
        "platform": "string", "ship_city_raw": "string", "city": "string", "sku_code": "string",
        "item_name": "string", "qty_ordered": "Int64", "qty_invoiced": "Int64",
        "qty_cancelled": "Int64", "item_price": "float", "mrp": "float", "reference_no": "string",
        "is_internal": "bool", "branch_id": "string",
    },
    "po_line": {
        "dataset_id": "string", "row_no": "Int64", "platform": "string", "po_number": "string",
        "po_date": "date", "status": "string", "facility": "string", "city_raw": "string",
        "city": "string", "platform_item_id": "string", "platform_name": "string", "ean": "string",
        "sku_code": "string", "qty_ordered": "Int64", "qty_received": "Int64", "qty_open": "Int64",
        "expected_date": "date", "expiry_date": "date", "unit_cost": "float", "mrp": "float",
    },
    "stock_snapshot": {
        "dataset_id": "string", "row_no": "Int64", "platform": "string", "snapshot_date": "date",
        "facility": "string", "city_raw": "string", "city": "string", "platform_item_id": "string",
        "platform_name": "string", "sku_code": "string", "soh_units": "Int64", "run_rate": "float",
        "doh": "float",
    },
    "market_daily": {
        "dataset_id": "string", "row_no": "Int64", "platform": "string", "market_date": "date",
        "variant_id": "string", "platform_item_id": "string", "sku_code": "string",
        "brand": "string", "is_own_brand": "bool", "category": "string", "product_name": "string",
        "pack_size": "string", "sales_mrp": "float", "sales_sp": "float", "mrp": "float",
        "sp": "float", "discount_pct": "float", "osa_pct": "float", "cat_share_mrp": "float",
        "cat_share_sp": "float", "city": "string",
    },
    "extra_value": {
        "dataset_id": "string", "row_no": "Int64", "variable": "string", "value_text": "string",
        "value_num": "float", "value_date": "date",
    },
    "sku_master": {
        "sku_code": "string", "item_name": "string", "plan_category": "string", "mrp": "float",
        "ean": "string", "pack_size": "string", "case_size": "Int64", "moq": "Int64",
    },
    "sku_crosswalk": {
        "platform": "string", "platform_item_id": "string", "platform_name": "string",
        "sku_code": "string", "match_method": "string", "score": "float", "candidates": "string",
        "confirmed_by": "string", "confirmed_at": "string",
    },
    "customer_map": {"customer_name": "string", "platform": "string", "rule": "string"},
    "city_map": {"raw_city": "string", "city": "string", "source": "string"},
}

# Natural keys used to de-duplicate across datasets (newest dataset wins).
NATURAL_KEYS: dict[str, list[str]] = {
    "sellin_line": ["line_key"],
    "po_line": ["platform", "po_number", "platform_item_id"],
    "stock_snapshot": ["platform", "snapshot_date", "facility", "platform_item_id"],
    "market_daily": ["platform", "market_date", "variant_id"],
}

# variable name -> table column, per file group
VAR_TO_COLUMN: dict[str, dict[str, str]] = {
    "sellin": {
        "sku_code": "sku_code", "item_name": "item_name", "order_date": "order_date",
        "so_status": "status", "customer_name": "customer_name", "ship_city": "ship_city_raw",
        "qty_invoiced": "qty_invoiced", "qty_cancelled": "qty_cancelled",
        "qty_ordered": "qty_ordered", "item_price": "item_price", "mrp": "mrp",
        "branch_id": "branch_id", "so_id": "so_id", "so_number": "so_number",
        "reference_no": "reference_no",
    },
    "po": {
        "po_number": "po_number", "po_date": "po_date", "po_status": "status",
        "facility": "facility", "po_city": "city_raw", "platform_item_id": "platform_item_id",
        "platform_item_name": "platform_name", "ean": "ean", "po_qty_ordered": "qty_ordered",
        "po_qty_received": "qty_received", "po_qty_open": "qty_open",
        "po_expected_date": "expected_date", "po_expiry_date": "expiry_date",
        "unit_cost": "unit_cost", "mrp": "mrp",
    },
    "stock": {
        "snapshot_date": "snapshot_date", "facility": "facility", "stock_city": "city_raw",
        "platform_item_id": "platform_item_id", "platform_item_name": "platform_name",
        "soh_units": "soh_units", "run_rate": "run_rate", "doh": "doh",
    },
    "market": {
        "market_date": "market_date", "variant_id": "variant_id",
        "platform_item_id": "platform_item_id", "brand": "brand", "category": "category",
        "product_name": "product_name", "pack_size": "pack_size", "sales_value_mrp": "sales_mrp",
        "sales_value_sp": "sales_sp", "mrp": "mrp", "selling_price": "sp",
        "discount_pct": "discount_pct", "osa_pct": "osa_pct",
        "category_share_mrp": "cat_share_mrp", "category_share_sp": "cat_share_sp",
        "market_city": "city",
    },
    "sku_master": {
        "sku_code": "sku_code", "item_name": "item_name", "plan_category": "plan_category",
        "mrp": "mrp", "ean": "ean", "pack_size": "pack_size", "case_size": "case_size",
        "moq": "moq",
    },
}

GROUP_TABLE = {"sellin": "sellin_line", "po": "po_line", "stock": "stock_snapshot",
               "market": "market_daily", "sku_master": "sku_master", "custom": "extra_value"}


def empty_frame(table: str) -> pd.DataFrame:
    return conform(pd.DataFrame(), table)


def conform(df: pd.DataFrame, table: str) -> pd.DataFrame:
    """Return ``df`` with exactly the table's columns and dtypes (missing → null)."""
    cols = TABLE_COLUMNS[table]
    out = pd.DataFrame(index=df.index)
    for col, kind in cols.items():
        s = df[col] if col in df.columns else pd.Series([None] * len(df), index=df.index, dtype="object")
        out[col] = _coerce(s, kind)
    return out.reset_index(drop=True)


def _coerce(s: pd.Series, kind: str) -> pd.Series:
    if kind == "string":
        return s.astype("object").where(s.notna(), None).map(
            lambda v: None if v is None else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))
        ).astype("string")
    if kind == "Int64":
        return pd.to_numeric(s, errors="coerce").round().astype("Int64")
    if kind == "float":
        return pd.to_numeric(s, errors="coerce").astype("float64")
    if kind == "bool":
        return s.map(lambda v: False if v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v))
                     else bool(v)).astype(bool)
    if kind == "date":
        return pd.to_datetime(s, errors="coerce").dt.normalize()
    raise ValueError(kind)
