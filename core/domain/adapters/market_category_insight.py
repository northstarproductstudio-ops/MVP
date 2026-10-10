"""Dcluttr category-insight export → core.market_daily."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from core.domain.adapters.base import Adapter, SourceSheet, parse_dates

CATEGORY_ALIASES = {"food colours": "Food Colour"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def platform_from_variant(v: str) -> str | None:
    v = str(v).strip()
    if re.fullmatch(r"\d+(\.0)?", v):
        return "blinkit"
    if UUID_RE.match(v):
        return "zepto"
    if re.fullmatch(r"[A-Z0-9]{10}", v, re.I):
        return "instamart"
    return None


class MarketCategoryInsightAdapter(Adapter):
    key = "market_category_insight"
    name = "Market data — Dcluttr category insight"
    group = "market"
    signature = ["Date", "Variant ID", "Item ID", "Brand", "Category", "Wt. OSA %", "Est Category Share SP"]
    header_rows = [3]
    filename_hints = ["dcluttr", "categories", "insight"]
    known_mapping = {
        "Date": "market_date", "Variant ID": "variant_id", "Item ID": "platform_item_id",
        "Product Name": "product_name", "Brand": "brand", "Category": "category",
        "Sales (MRP)": "sales_value_mrp", "Est Sales MRP": "sales_value_mrp",
        "Sales (SP)": "sales_value_sp", "Est Sales SP": "sales_value_sp", "Raw Units": "pack_size",
        "Est Category Share MRP": "category_share_mrp", "Est Category Share SP": "category_share_sp",
        "MRP": "mrp", "SP": "selling_price", "WT Discount": "discount_pct", "Wt. OSA %": "osa_pct",
        "City": "market_city",
    }
    tier2_mapping = {
        "Overall SOV": ("share_of_voice", "Visibility"),
        "Organic SOV": ("organic_sov", "Visibility"),
        "Ad SOV": ("ad_sov", "Visibility"),
        "WT PPU (x100)": ("price_per_unit_x100", "Pricing"),
    }

    def post_fix(self, df: pd.DataFrame, src: SourceSheet, ctx: dict[str, Any],
                 warnings: list[str]) -> pd.DataFrame:
        df = df.copy()
        df["market_date"] = parse_dates(df.get("market_date"))
        df["variant_id"] = df["variant_id"].astype("string").str.replace(r"\.0$", "", regex=True).str.strip()
        plat = df["variant_id"].dropna().map(platform_from_variant).value_counts()
        platform = plat.index[0] if len(plat) else None
        hint = (ctx.get("file_name") or "").lower()
        for p in ("blinkit", "instamart", "zepto"):
            if p in hint:
                if platform and platform != p:
                    warnings.append(f"Variant IDs look like {platform} but the file name says {p}; using {p}")
                platform = p
        if platform is None:
            raise ValueError("cannot tell the platform from Variant IDs or the file name")
        df["platform"] = platform
        if "platform_item_id" in df:
            pid = df["platform_item_id"].astype("string").str.replace(r"\.0$", "", regex=True).str.strip()
            df["platform_item_id"] = pid.where(pid.notna() & (pid != ""), df["variant_id"])
        else:
            df["platform_item_id"] = df["variant_id"]
        if platform == "zepto":
            df["platform_item_id"] = df["platform_item_id"].str.lower()
        if "category" in df:
            cat = df["category"].astype("string").str.strip()
            mapped = cat.str.lower().map(CATEGORY_ALIASES)
            n = int(mapped.notna().sum())
            if n:
                warnings.append(f"Category aliases applied on {n} rows (e.g. Food Colours → Food Colour)")
            df["category"] = mapped.fillna(cat)
        renamed = [c for c in src.frame.columns if c.lower().startswith("est sales")]
        if renamed:
            warnings.append("Est Sales MRP/SP read as Sales (MRP)/(SP)")
        own = [n.lower() for n in ctx.get("own_brand_names", [])]
        df["is_own_brand"] = df.get("brand", pd.Series(dtype="string")).astype("string").str.strip() \
            .str.lower().isin(own)
        return df
