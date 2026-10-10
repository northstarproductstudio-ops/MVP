"""Zoho sales-order export → core.sellin_line."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from core.domain.adapters.base import Adapter, SourceSheet, parse_dates
from core.domain.file_reader import norm_key, read_sheet


class ErpSalesOrdersAdapter(Adapter):
    key = "erp_sales_orders"
    name = "ERP sales orders (Zoho)"
    group = "sellin"
    signature = ["SalesOrder ID", "Order Date", "Status", "Customer Name", "SKU",
                 "QuantityOrdered", "Branch ID"]
    header_rows = [1]
    filename_hints = ["so", "sales order", "salesorder", "zoho"]
    known_mapping = {
        "SalesOrder ID": "so_id", "Order Date": "order_date", "SalesOrder Number": "so_number",
        "Status": "so_status", "Customer Name": "customer_name", "Branch ID": "branch_id",
        "Reference#": "reference_no", "Item Name": "item_name", "SKU": "sku_code",
        "QuantityOrdered": "qty_ordered", "QuantityInvoiced": "qty_invoiced",
        "QuantityCancelled": "qty_cancelled", "Item Price": "item_price",
        "Shipping City": "ship_city", "Item.CF.MRP": "mrp",
    }
    tier2_mapping = {
        "Expected Shipment Date": ("expected_shipment_date", "Time"),
        "Place of Supply": ("place_of_supply", "Store locations"),
        "Warehouse Name": ("warehouse_name", "Store locations"),
        "Usage unit": ("usage_unit", "Product"),
        "Shipping State": ("ship_state", "Store locations"),
    }

    ORG2_PREFIX = "3854"
    SHIFT_FROM = "Warehouse Name"
    DROP_STATUSES = ("void", "draft")

    def read_frame(self, path: Path, sheet: str, header_row: int) -> pd.DataFrame:
        """Positional read so the org-2 shift is repaired before columns get names."""
        df = read_sheet(path, sheet, header_row, raw_positions=True)
        names: list[str] = df.attrs.get("header_names") or [str(c) for c in df.columns]
        by_key = {norm_key(n): i for i, n in enumerate(names)}
        b = by_key.get(norm_key("Branch ID"))
        w = by_key.get(norm_key(self.SHIFT_FROM))
        shifted_rows = 0
        if b is not None and w is not None and len(df):
            df = df.astype(object)
            mask = df[b].map(lambda v: str(v).startswith(self.ORG2_PREFIX) if v is not None else False)
            shifted_rows = int(mask.sum())
            if shifted_rows:
                cols = [c for c in df.columns if c >= w]
                df.loc[mask, cols[:-1]] = df.loc[mask, cols[1:]].to_numpy()
                df.loc[mask, cols[-1]] = None
        df.columns = names[: len(df.columns)]
        df.attrs = {"shifted_rows": shifted_rows}
        return df

    def pre_fix(self, src: SourceSheet, warnings: list[str]) -> pd.DataFrame:
        n = src.frame.attrs.get("shifted_rows", 0)
        if n:
            warnings.append(
                f"{n} rows from branch {self.ORG2_PREFIX}… had every column from "
                f"{self.SHIFT_FROM} onward shifted one place left (org-2 export shift)")
        return src.frame

    STATES = {
        "andhra pradesh", "assam", "bihar", "chhattisgarh", "delhi", "goa", "gujarat", "haryana",
        "himachal pradesh", "jharkhand", "karnataka", "kerala", "madhya pradesh", "maharashtra", "odisha",
        "punjab", "rajasthan", "tamil nadu", "telangana", "uttar pradesh", "uttarakhand", "west bengal",
    }

    def _fix_address_shift(self, df: pd.DataFrame, src: pd.DataFrame, warnings: list[str]) -> pd.DataFrame:
        """Some org-2 rows carry the address block one more column to the right: Shipping
        City holds the state, Shipping State 'India' and the city sits in Shipping Street2."""
        cols = {norm_key(c): c for c in src.columns}
        city_c, st2_c, state_c = cols.get("shippingcity"), cols.get("shippingstreet2"), cols.get("shippingstate")
        if not (city_c and st2_c and state_c) or "ship_city_raw" not in df:
            return df
        s = src.loc[src.index.intersection(df.index)]
        city = s[city_c].astype("string").str.strip().str.lower()
        state = s[state_c].astype("string").str.strip().str.lower()
        mask = city.isin(self.STATES) & state.eq("india")
        n = int(mask.sum())
        if n:
            df = df.copy()
            df.loc[mask[mask].index, "ship_city_raw"] = s.loc[mask, st2_c]
            warnings.append(f"{n} rows had the shipping address one column right (state in Shipping City); "
                            "city read from Shipping Street2")
        return df

    def post_fix(self, df: pd.DataFrame, src: SourceSheet, ctx: dict[str, Any],
                 warnings: list[str]) -> pd.DataFrame:
        df = df.copy()
        status = df.get("status")
        if status is not None:
            st = status.astype("string").str.strip().str.lower()
            df["status"] = st
            drop = st.isin(self.DROP_STATUSES)
            if int(drop.sum()):
                counts = st[drop].value_counts().to_dict()
                warnings.append("Dropped " + ", ".join(f"{v} {k}" for k, v in counts.items())
                                + " order lines (not sell-in)")
            df = df[~drop]
        df["order_date"] = parse_dates(df["order_date"]) if "order_date" in df else pd.NaT
        bad = df["order_date"].isna().sum() if "order_date" in df else 0
        if bad:
            warnings.append(f"{int(bad)} rows have an order date that does not parse")
        for c in ("qty_invoiced", "qty_cancelled", "qty_ordered"):
            if c in df:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "qty_cancelled" not in df:
            df["qty_cancelled"] = 0
        df["qty_cancelled"] = df["qty_cancelled"].fillna(0)
        src_frame = src.frame
        unit_col = next((c for c in src_frame.columns if norm_key(c) == "usageunit"), None)
        if unit_col is not None:
            kg = src_frame.loc[src_frame.index.intersection(df.index), unit_col].astype("string") \
                .str.lower().eq("kg").sum()
            if kg:
                warnings.append(f"{int(kg)} lines use usage unit kg; kept as-is and flagged")
        df = self._fix_address_shift(df, src_frame, warnings)
        df["sku_code"] = df["sku_code"].astype("string").str.strip()
        missing_sku = df["sku_code"].isna() | (df["sku_code"] == "")
        if int(missing_sku.sum()):
            warnings.append(f"{int(missing_sku.sum())} lines have no SKU and were dropped")
            df = df[~missing_sku]
        # natural key: sales-order id + sku + occurrence within that order
        so = df.get("so_id", pd.Series(index=df.index, dtype="object")).astype("string").fillna("")
        occ = df.groupby([so, df["sku_code"]]).cumcount()
        df["line_key"] = so + "|" + df["sku_code"] + "|" + occ.astype(str)
        return df
