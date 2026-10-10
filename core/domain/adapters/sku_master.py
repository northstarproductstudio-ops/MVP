"""SKU master (optional) → core.sku_master; and the Custom type."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.domain.adapters.base import Adapter, SourceSheet
from core.domain.file_reader import norm_key


class SkuMasterAdapter(Adapter):
    key = "sku_master"
    name = "SKU master"
    group = "sku_master"
    signature = ["SKU", "case size", "MOQ", "category"]
    header_rows = [1]
    filename_hints = ["sku master", "master"]
    known_mapping = {
        "SKU": "sku_code", "SKU Code": "sku_code", "Item Name": "item_name", "Product": "item_name",
        "Category": "plan_category", "Plan Category": "plan_category", "MRP": "mrp", "EAN": "ean",
        "Pack Size": "pack_size", "Case Size": "case_size", "Case Pack": "case_size", "MOQ": "moq",
    }

    @classmethod
    def score_headers(cls, headers: list[str]) -> float:
        have = {norm_key(h) for h in headers if h}
        if not ({"sku", "skucode"} & have):
            return 0.0
        extras = {"casesize", "casepack", "moq", "category", "plancategory"} & have
        return 0.85 if extras else 0.25

    def post_fix(self, df: pd.DataFrame, src: SourceSheet, ctx: dict[str, Any],
                 warnings: list[str]) -> pd.DataFrame:
        df = df.copy()
        df["sku_code"] = df["sku_code"].astype("string").str.strip()
        df = df[df["sku_code"].notna() & (df["sku_code"] != "")]
        return df.drop_duplicates("sku_code", keep="last")


class CustomAdapter(Adapter):
    key = "custom"
    name = "Custom"
    group = "custom"
    signature = []

    def transform(self, sources, mapping, ctx):  # type: ignore[override]
        from core.domain.adapters.base import TransformResult, extra_values
        from core.domain.tables import conform, empty_frame

        extras = []
        for src in sources:
            tier = {f: v for (s, f), v in mapping.items() if s == src.sheet and v and f in src.frame.columns}
            if tier:
                extras.append(extra_values(src.frame, tier))
        extra = pd.concat(extras, ignore_index=True) if extras else pd.DataFrame()
        extra["dataset_id"] = ctx.get("dataset_id")
        return TransformResult(table="extra_value", frame=empty_frame("extra_value"),
                               extra=conform(extra, "extra_value"))
