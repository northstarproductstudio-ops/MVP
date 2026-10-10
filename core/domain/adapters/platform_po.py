"""Platform purchase-order adapters → core.po_line (Instamart, Zepto, BigBasket)."""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from core.domain.adapters.base import Adapter, SourceSheet, parse_dates
from core.domain.file_reader import RawSheet, headers_at, norm_key

STATUS_MAP = {
    "completed": "completed", "complete": "completed", "closed": "completed", "grn done": "completed",
    "expired": "expired",
    "confirmed": "open", "open": "open", "created": "open", "pending": "open",
    "partially received": "open", "in transit": "open",
    "cancelled": "cancelled", "canceled": "cancelled",
}


def _norm_status(s: pd.Series) -> pd.Series:
    return s.astype("string").str.strip().str.lower().map(lambda v: STATUS_MAP.get(v, v) if v else v)


class _PoBase(Adapter):
    group = "po"
    platform: ClassVar[str] = ""
    tier2_mapping = {}

    def post_fix(self, df: pd.DataFrame, src: SourceSheet, ctx: dict[str, Any],
                 warnings: list[str]) -> pd.DataFrame:
        df = df.copy()
        df["platform"] = self.platform
        for c in ("qty_ordered", "qty_received", "qty_open"):
            if c in df:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "status" in df:
            df["status"] = _norm_status(df["status"])
        df = self.platform_fix(df, src, ctx, warnings)
        unknown = set(df["status"].dropna().unique()) - {"completed", "expired", "open", "cancelled"} \
            if "status" in df else set()
        if unknown:
            warnings.append(f"Unrecognised PO statuses treated as open: {sorted(unknown)}")
            df.loc[df["status"].isin(unknown), "status"] = "open"
        if "status" in df and "qty_open" not in df:
            df["qty_open"] = 0
        cancelled = int((df.get("status") == "cancelled").sum()) if "status" in df else 0
        if cancelled:
            warnings.append(f"{cancelled} cancelled PO lines kept but excluded from fill rate and demand")
        before = len(df)
        df = df.drop_duplicates(subset=["po_number", "platform_item_id"], keep="last")
        if len(df) < before:
            warnings.append(f"{before - len(df)} duplicate PO lines (PO number + SKU) collapsed")
        return df

    def platform_fix(self, df: pd.DataFrame, src: SourceSheet, ctx: dict[str, Any],
                     warnings: list[str]) -> pd.DataFrame:
        return df


class PlatformPoInstamartAdapter(_PoBase):
    key = "platform_po_instamart"
    name = "Platform PO — Instamart (Swiggy)"
    platform = "instamart"
    signature = ["PoNumber", "FacilityId", "City", "OrderedQty", "ReceivedQty", "BalancedQty", "SkuCode"]
    header_rows = [1]
    filename_hints = ["swiggy", "instamart"]
    known_mapping = {
        "PoNumber": "po_number", "PoCreatedAt": "po_date", "Status": "po_status",
        "FacilityName": "facility", "City": "po_city", "SkuCode": "platform_item_id",
        "SkuDescription": "platform_item_name", "OrderedQty": "po_qty_ordered",
        "ReceivedQty": "po_qty_received", "BalancedQty": "po_qty_open",
        "ExpectedDeliveryDate": "po_expected_date", "PoExpiryDate": "po_expiry_date",
        "UnitBasedCost": "unit_cost", "Mrp": "mrp",
    }
    tier2_mapping = {"PoAgeing": ("po_ageing_days", "Purchase orders"),
                     "CategoryId": ("platform_category", "Product")}

    def platform_fix(self, df, src, ctx, warnings):
        df["po_date"] = parse_dates(df.get("po_date"))
        df["expected_date"] = parse_dates(df.get("expected_date"))
        df["expiry_date"] = parse_dates(df.get("expiry_date"))
        if "city_raw" in df:
            df["city_raw"] = df["city_raw"].astype("string").str.strip().str.title()
            warnings.append("City converted from upper case to title case")
        # Open qty = BalancedQty on CONFIRMED (now 'open') rows only.
        if "qty_open" in df:
            df["qty_open"] = df["qty_open"].where(df["status"] == "open", 0)
        return df


class PlatformPoZeptoAdapter(_PoBase):
    key = "platform_po_zepto"
    name = "Platform PO — Zepto"
    platform = "zepto"
    signature = ["PO No.", "Del Location", "SKU", "EAN", "Qty", "ASN Quantity", "GRN Quantity"]
    header_rows = [1]
    filename_hints = ["zepto"]
    known_mapping = {
        "PO No.": "po_number", "PO Date": "po_date", "Status": "po_status",
        "Del Location": "facility", "SKU": "platform_item_id", "SKU Desc": "platform_item_name",
        "EAN": "ean", "Qty": "po_qty_ordered", "GRN Quantity": "po_qty_received",
        "PO Expiry Date": "po_expiry_date", "Landing Cost": "unit_cost", "MRP/RSP": "mrp",
    }
    tier2_mapping = {"ASN Quantity": ("asn_quantity", "Purchase orders"),
                     "Created By": ("po_created_by", "Purchase orders")}
    DATE_FMT = "%d %b %Y %I:%M %p"

    def platform_fix(self, df, src, ctx, warnings):
        df["po_date"] = parse_dates(df.get("po_date"), self.DATE_FMT)
        df["expiry_date"] = parse_dates(df.get("expiry_date"), self.DATE_FMT)
        if "platform_item_id" in df:
            df["platform_item_id"] = df["platform_item_id"].astype("string").str.strip().str.lower()
            warnings.append("SKU UUIDs lower-cased")
        if "facility" in df:
            # City from the Del Location prefix (CHN, MUM, BLR …) via the city map
            df["city_raw"] = df["facility"].astype("string").str.split("-").str[0].str.strip()
        if "ean" in df:
            df["ean"] = df["ean"].astype("string").str.replace(r"\.0$", "", regex=True).str.strip()
        if "qty_ordered" in df:
            recv = df.get("qty_received", 0)
            df["qty_open"] = (df["qty_ordered"] - pd.to_numeric(recv, errors="coerce").fillna(0)) \
                .clip(lower=0).where(df["status"] == "open", 0)
        return df


class PlatformPoBigbasketAdapter(_PoBase):
    key = "platform_po_bigbasket"
    name = "Platform PO / fill rate — BigBasket"
    platform = "bigbasket"
    signature = ["po_no", "source_sku_id", "po_quantity", "grn_quantity", "quantity_fillrate", "po_type"]
    header_rows = [1]
    filename_hints = ["bb", "bigbasket", "fr"]
    known_mapping = {
        "po_no": "po_number", "po_creation_date_id": "po_date", "po_type": "po_status",
        "location_name": "facility", "source_city_name": "po_city",
        "source_sku_id": "platform_item_id", "sku_description": "platform_item_name",
        "po_quantity": "po_qty_ordered", "grn_quantity": "po_qty_received",
        "po_delivery_date_id": "po_expected_date", "po_expiry_date_id": "po_expiry_date",
        "unit_cost_price": "unit_cost", "unit_mrp": "mrp",
    }
    tier2_mapping = {"LLC": ("platform_category", "Product"),
                     "tier_flag": ("city_tier", "Store locations")}
    DATA_SHEETS = ("fr data", "open po")

    @classmethod
    def sheet_is_data(cls, sheet: RawSheet, rows_to_try: int = 5) -> bool:
        return sheet.name.strip().lower() in cls.DATA_SHEETS or super().sheet_is_data(sheet, rows_to_try)

    def platform_fix(self, df, src, ctx, warnings):
        for c in ("po_date", "expected_date", "expiry_date"):
            df[c] = parse_dates(df.get(c), "%d-%m-%Y")
        if src.sheet.strip().lower() == "open po":
            df["status"] = df["status"].fillna("open")
            df["qty_open"] = df["qty_ordered"].where(df["status"] == "open", 0)
        else:
            df["qty_open"] = df["qty_ordered"].where(df["status"] == "open", 0)
        df["po_number"] = df["po_number"].astype("string").str.strip()
        return df

    def read(self, path, sheets, rows_to_try=5):
        from core.domain.adapters.base import SourceSheet as Src

        out = []
        for sh in sheets:
            if sh.name.strip().lower() not in self.DATA_SHEETS:
                continue  # summary sheets skipped
            hdr = next((r for r in range(1, rows_to_try + 1)
                        if self.score_headers(headers_at(sh, r)) >= 0.8), 1)
            out.append(Src(sh.name, hdr, self.read_frame(path, sh.name, hdr)))
        return out


def _norm(c: str) -> str:
    return norm_key(c)
