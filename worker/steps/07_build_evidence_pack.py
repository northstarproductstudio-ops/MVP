"""Step 7 — build_evidence_pack: one JSON pack per SKU plus the reference DP per line."""

from __future__ import annotations

import hashlib
import json

import pandas as pd

from core.domain.plan_math import line_factors, reference_dp
from worker.context import RunContext
from worker.steps import StepResult, step


@step(name="build_evidence_pack", no=7,
      reads=["history", "classes", "base", "factors", "signals"],
      writes=["evidence_pack", "reference"],
      config_keys=["agent.show_reference", "history.months"], version="1.0")
def build_evidence_pack(ctx: RunContext) -> StepResult:
    hist = ctx.artifact("history")
    classes = ctx.artifact("classes").set_index("sku_code")
    base = ctx.artifact("base")
    factors = ctx.artifact("factors")
    signals = ctx.artifact("signals").set_index("line_id")
    months = list(reversed(ctx.history_months()))  # oldest → newest
    labels = [f"{m:%Y-%m}" for m in months]
    show_ref = bool(ctx.config.get("agent.show_reference"))
    line_h = hist.groupby(["sku_code", "city", "platform", "month"])["q"].sum()
    nat_h = hist.groupby(["sku_code", "platform", "month"])["q"].sum()

    packs, refs = [], []
    for sku, grp in base.sort_values(["sku_code", "platform", "city"]).groupby("sku_code", sort=True):
        lines = []
        sku_flags: set[str] = set()
        for r in grp.itertuples():
            fs = line_factors(factors, r.sku_code, r.city, r.platform)
            ref = reference_dp(r.base, fs)
            sig = json.loads(signals.loc[r.line_id, "signals"]) if r.line_id in signals.index else {}
            fl = json.loads(signals.loc[r.line_id, "flags"]) if r.line_id in signals.index else []
            sku_flags.update(fl)
            refs.append({"line_id": r.line_id, "sku_code": r.sku_code, "city": r.city,
                         "platform": r.platform, "base": float(r.base), "reference_dp": ref,
                         "python_factors": json.dumps(fs, sort_keys=True), "flags": json.dumps(fl)})
            line = {
                "line_id": r.line_id, "city": r.city, "platform": r.platform, "base": round(float(r.base), 2),
                "factors": [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in f.items()} for f in fs],
                "signals": sig,
                "history": {"months": labels,
                            "line": [float(line_h.get((sku, r.city, r.platform, m), 0.0)) for m in months],
                            "national": [float(nat_h.get((sku, r.platform, m), 0.0)) for m in months]},
            }
            if show_ref:
                line["reference_dp"] = ref
            lines.append(line)
        pack = {
            "sku": sku, "name": grp["item_name"].iloc[0], "plan_category": grp["plan_category"].iloc[0],
            "abc": classes["abc"].get(sku), "sb_class": classes["sb_class"].get(sku),
            "target_month": f"{ctx.target_month:%Y-%m-%d}", "as_of": f"{ctx.as_of:%Y-%m-%d}",
            "horizon_months": ctx.horizon, "lines": lines, "flags": sorted(sku_flags),
        }
        text = json.dumps(pack, sort_keys=True, default=str)
        packs.append({"sku_code": sku, "pack_json": text, "pack_hash": hashlib.sha256(text.encode()).hexdigest(),
                      "n_lines": len(lines)})
    ev = pd.DataFrame(packs)
    ref_df = pd.DataFrame(refs)
    total = int(ref_df["reference_dp"].sum()) if len(ref_df) else 0
    return StepResult(rows_in=len(base), rows_out=len(ev),
                      summary=f"{len(ev)} evidence packs, {len(ref_df)} lines; reference DP total {total:,} units",
                      artifacts={"evidence_pack": ev, "reference": ref_df})
