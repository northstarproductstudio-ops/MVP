"""Back-test scoring: WAPE = Σ|plan − actual| ÷ Σ actual; bias = Σ(plan − actual) ÷ Σ actual;
overall and per ABC class; naive benchmark "M1 = M2"; FVA = reference WAPE − agent WAPE;
and a factor ablation (factors whose removal lowers WAPE)."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from core.domain.plan_math import reference_dp


def wape(plan: pd.Series, actual: pd.Series) -> float | None:
    tot = float(actual.sum())
    return float((plan - actual).abs().sum() / tot) if tot else None


def bias(plan: pd.Series, actual: pd.Series) -> float | None:
    tot = float(actual.sum())
    return float((plan - actual).sum() / tot) if tot else None


def score(lines: pd.DataFrame, actual_by_line: dict[str, float], naive_by_line: dict[str, float]
          ) -> list[dict[str, Any]]:
    """``lines``: line_id, abc, reference_dp, final_dp, base, python_factors (json)."""
    df = lines.copy()
    all_ids = set(df["line_id"]) | set(actual_by_line)
    df = df.set_index("line_id").reindex(sorted(all_ids))
    df["actual"] = pd.Series(actual_by_line).reindex(df.index).fillna(0.0)
    df["naive"] = pd.Series(naive_by_line).reindex(df.index).fillna(0.0)
    for c in ("reference_dp", "final_dp"):
        df[c] = df[c].fillna(0.0)
    df["abc"] = df["abc"].fillna("C")
    out: list[dict[str, Any]] = []

    def add(metric: str, cls: str | None, value: float | None) -> None:
        if value is not None:
            out.append({"metric": metric, "class": cls, "value": round(value, 6)})

    groups = [(None, df)] + [(k, g) for k, g in df.groupby("abc")]
    for cls, g in groups:
        add("wape_reference", cls, wape(g["reference_dp"], g["actual"]))
        add("wape_agent", cls, wape(g["final_dp"], g["actual"]))
        add("wape_naive", cls, wape(g["naive"], g["actual"]))
        add("bias_reference", cls, bias(g["reference_dp"], g["actual"]))
        add("bias_agent", cls, bias(g["final_dp"], g["actual"]))
        wr, wa = wape(g["reference_dp"], g["actual"]), wape(g["final_dp"], g["actual"])
        if wr is not None and wa is not None:
            add("fva", cls, wr - wa)
    # ablation: drop one Python factor at a time from the reference DP
    base_w = wape(df["reference_dp"], df["actual"])
    names = set()
    for pf in df["python_factors"].dropna():
        names |= {f["name"] for f in json.loads(pf)}
    for n in sorted(names):
        alt = []
        for r in df.itertuples():
            fs = json.loads(r.python_factors) if isinstance(r.python_factors, str) else []
            alt.append(reference_dp(r.base if pd.notna(r.base) else 0.0, [f for f in fs if f["name"] != n]))
        w = wape(pd.Series(alt, index=df.index), df["actual"])
        if w is not None and base_w is not None:
            add(f"ablation_delta:{n}", None, w - base_w)  # negative → removing the factor lowers WAPE
    return out
