"""Adapter contract: one class per file type.

``detect(sheet) -> (score, header_row)`` scores a sheet against the column signature,
``read(path)`` returns the sheets with their source frames, and ``transform`` turns a
mapped source frame into one typed core table, logging every fix it applies as a warning
the user sees on MVP-09.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd

from core.domain.file_reader import RawSheet, headers_at, norm_key, read_sheet
from core.domain.tables import GROUP_TABLE, VAR_TO_COLUMN, conform


@dataclass
class SourceSheet:
    sheet: str
    header_row: int
    frame: pd.DataFrame  # source columns, raw values; index = source row number


@dataclass
class TransformResult:
    table: str
    frame: pd.DataFrame
    warnings: list[str] = field(default_factory=list)
    extra: pd.DataFrame | None = None  # tier-2 values for core.extra_value
    meta: dict[str, Any] = field(default_factory=dict)


class Adapter:
    key: ClassVar[str] = ""
    name: ClassVar[str] = ""
    group: ClassVar[str] = ""
    signature: ClassVar[list[str]] = []
    header_rows: ClassVar[list[int]] = [1, 2, 3, 4, 5]
    filename_hints: ClassVar[list[str]] = []
    # source column (as in file) -> variable name
    known_mapping: ClassVar[dict[str, str]] = {}
    # source column -> (tier-2 variable name, category)
    tier2_mapping: ClassVar[dict[str, tuple[str, str]]] = {}

    # ---------------------------------------------------------------- detection
    @classmethod
    def score_headers(cls, headers: list[str]) -> float:
        if not cls.signature:
            return 0.0
        have = {norm_key(h) for h in headers if h}
        hit = sum(1 for s in cls.signature if norm_key(s) in have)
        return hit / len(cls.signature)

    @classmethod
    def detect(cls, sheet: RawSheet, rows_to_try: int = 5) -> tuple[float, int]:
        best = (0.0, 1)
        for r in range(1, min(rows_to_try, len(sheet.preview)) + 1):
            s = cls.score_headers(headers_at(sheet, r))
            if s > best[0]:
                best = (s, r)
        return best

    @classmethod
    def sheet_is_data(cls, sheet: RawSheet, rows_to_try: int = 5) -> bool:
        """Sheets that carry this type's rows (summary sheets are skipped)."""
        return cls.detect(sheet, rows_to_try)[0] >= 0.8

    # ---------------------------------------------------------------- reading
    def read(self, path: Path, sheets: list[RawSheet], rows_to_try: int = 5) -> list[SourceSheet]:
        out = []
        for sh in sheets:
            score, hdr = self.detect(sh, rows_to_try)
            if score >= 0.8:
                out.append(SourceSheet(sh.name, hdr, self.read_frame(path, sh.name, hdr)))
        return out

    def read_frame(self, path: Path, sheet: str, header_row: int) -> pd.DataFrame:
        return read_sheet(path, sheet, header_row)

    # ---------------------------------------------------------------- mapping
    @classmethod
    def mapping_for(cls, columns: list[str]) -> dict[str, str]:
        """Adapter's tier-1 mapping restricted to columns present (case-insensitive)."""
        by_key = {norm_key(c): c for c in columns}
        out = {}
        for src, var in cls.known_mapping.items():
            col = by_key.get(norm_key(src))
            if col is not None and var not in out.values():
                out[col] = var
        return out

    @classmethod
    def tier2_for(cls, columns: list[str]) -> dict[str, tuple[str, str]]:
        by_key = {norm_key(c): c for c in columns}
        return {by_key[norm_key(s)]: v for s, v in cls.tier2_mapping.items() if norm_key(s) in by_key}

    # ---------------------------------------------------------------- transform
    def pre_fix(self, src: SourceSheet, warnings: list[str]) -> pd.DataFrame:
        """Repairs on source columns before mapping (e.g. column shifts)."""
        return src.frame

    def post_fix(self, df: pd.DataFrame, src: SourceSheet, ctx: dict[str, Any],
                 warnings: list[str]) -> pd.DataFrame:
        """Type-specific cleaning on table columns after mapping."""
        return df

    def transform(self, sources: list[SourceSheet], mapping: dict[tuple[str, str], str],
                  ctx: dict[str, Any]) -> TransformResult:
        """``mapping``: (sheet, source_field) -> variable (tier 1 and tier 2)."""
        warnings: list[str] = []
        table = GROUP_TABLE[self.group]
        var_to_col = VAR_TO_COLUMN.get(self.group, {})
        frames = []
        extras = []
        for src in sources:
            fixed = self.pre_fix(src, warnings)
            mapped = pd.DataFrame(index=fixed.index)
            tier2: dict[str, str] = {}
            for (sheet, field_name), var in mapping.items():
                if sheet != src.sheet or field_name not in fixed.columns or not var:
                    continue
                col = var_to_col.get(var)
                if col:
                    mapped[col] = fixed[field_name]
                else:
                    tier2[field_name] = var
            mapped["row_no"] = fixed.index
            mapped["_sheet"] = src.sheet
            mapped = self.post_fix(mapped, src, ctx, warnings)
            frames.append(mapped)
            if tier2:
                extras.append(extra_values(fixed.loc[mapped.index.intersection(fixed.index)], tier2))
        df = pd.concat(frames, ignore_index=False) if frames else pd.DataFrame()
        df["dataset_id"] = ctx.get("dataset_id")
        out = conform(df, table)
        extra = pd.concat(extras, ignore_index=True) if extras else None
        if extra is not None:
            extra["dataset_id"] = ctx.get("dataset_id")
            extra = conform(extra, "extra_value")
        return TransformResult(table=table, frame=out, warnings=warnings, extra=extra)


def extra_values(src: pd.DataFrame, tier2: dict[str, str]) -> pd.DataFrame:
    """Long-format tier-2 values: one row per (source row, variable)."""
    rows = []
    for field_name, var in tier2.items():
        for row_no, v in src[field_name].items():
            if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() in ("", "-"):
                continue
            num = _to_float(v)
            date = v if hasattr(v, "year") else None
            rows.append({"row_no": row_no, "variable": var, "value_text": str(v),
                         "value_num": num, "value_date": date})
    return pd.DataFrame(rows, columns=["row_no", "variable", "value_text", "value_num", "value_date"])


def _to_float(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def null_dash(s: pd.Series) -> pd.Series:
    """'-' and blanks become null, never 0."""
    return s.map(lambda v: None if v is None or (isinstance(v, str) and v.strip() in ("", "-")) else v)


def parse_dates(s: pd.Series | None, fmt: str | None = None, dayfirst: bool = False) -> pd.Series | None:
    if s is None:
        return None
    if fmt:
        if pd.api.types.is_datetime64_any_dtype(s):
            return s
        return pd.to_datetime(s.astype("string").str.strip().str.upper(), format=fmt, errors="coerce")
    return pd.to_datetime(s, errors="coerce", dayfirst=dayfirst)
