"""Blinkit DOH/SOH export → core.stock_snapshot."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from core.domain.adapters.base import Adapter, SourceSheet, null_dash, parse_dates
from core.domain.file_reader import RawSheet, norm_key, read_sheet


class PlatformStockBlinkitAdapter(Adapter):
    key = "platform_stock_blinkit"
    name = "Platform stock (DOH/SOH) — Blinkit"
    group = "stock"
    signature = ["backend_facility_id", "item_id", "backend_inv_qty", "frontend_inv_qty", "RR", "DOH"]
    header_rows = [2]
    filename_hints = ["blinkit", "doh", "soh"]
    known_mapping = {
        "created_at": "snapshot_date", "backend_facility_name": "facility", "City": "stock_city",
        "item_id": "platform_item_id", "item_name": "platform_item_name", "Total SOH": "soh_units",
        "RR": "run_rate", "DOH": "doh",
    }
    tier2_mapping = {}
    RR_LOOKUP_SIG = ("itemidxcity", "rr")

    def read(self, path: Path, sheets: list[RawSheet], rows_to_try: int = 5) -> list[SourceSheet]:
        out = super().read(path, sheets, rows_to_try)
        self._rr_lookup: pd.DataFrame | None = None
        for sh in sheets:
            if sh.preview and [norm_key(v) for v in sh.preview[0][:2]] == list(self.RR_LOOKUP_SIG):
                lk = read_sheet(path, sh.name, 1)
                lk.columns = ["key", "rr"] + list(lk.columns[2:])
                self._rr_lookup = lk[["key", "rr"]]
        return out

    def read_frame(self, path: Path, sheet: str, header_row: int) -> pd.DataFrame:
        df = read_sheet(path, sheet, header_row)
        # side columns (Q–W) start after the first blank header: ignore them
        cols = list(df.columns)
        first_blank = next((i for i, c in enumerate(cols) if c.startswith("col_")), len(cols))
        return df[cols[:first_blank]]

    def post_fix(self, df: pd.DataFrame, src: SourceSheet, ctx: dict[str, Any],
                 warnings: list[str]) -> pd.DataFrame:
        df = df.copy()
        df["platform"] = "blinkit"
        df["snapshot_date"] = parse_dates(df.get("snapshot_date"))
        for c in ("run_rate", "doh"):
            if c in df:
                n_dash = int(df[c].astype("string").str.strip().eq("-").sum())
                df[c] = pd.to_numeric(null_dash(df[c]), errors="coerce")
                if n_dash:
                    warnings.append(f'{n_dash} "-" values in {c} stored as blank, not 0')
        f = src.frame
        b = next((c for c in f.columns if norm_key(c) == "backend_inv_qty"), None)
        fr = next((c for c in f.columns if norm_key(c) == "frontend_inv_qty"), None)
        if b and fr:
            soh = pd.to_numeric(f[b], errors="coerce").fillna(0) + pd.to_numeric(f[fr], errors="coerce").fillna(0)
            df["soh_units"] = soh.loc[df.index]
            warnings.append("SOH computed as backend + frontend inventory")
        lk = getattr(self, "_rr_lookup", None)
        if lk is not None and "run_rate" in df and "platform_item_id" in df:
            key = df["platform_item_id"].astype("string").str.replace(r"\.0$", "", regex=True) + \
                df.get("city_raw", pd.Series("", index=df.index)).astype("string").fillna("")
            rr_map = dict(zip(lk["key"].astype(str), pd.to_numeric(lk["rr"], errors="coerce"), strict=False))
            fill = df["run_rate"].isna() & key.map(rr_map).notna()
            if int(fill.sum()):
                df.loc[fill, "run_rate"] = key[fill].map(rr_map)
                warnings.append(f"{int(fill.sum())} null run rates filled from the RR lookup sheet")
        if "doh" in df and "run_rate" in df:
            recompute = df["doh"].isna() & (df["run_rate"] > 0)
            df.loc[recompute, "doh"] = df.loc[recompute, "soh_units"] / df.loc[recompute, "run_rate"]
        df["platform_item_id"] = df["platform_item_id"].astype("string").str.replace(r"\.0$", "", regex=True)
        return df
