"""Step 7 — build_evidence_pack: one JSON pack per SKU plus the reference DP per line, and
the planning-agent batches (Gaps 3–5): one batch per plan category, split into sub-batches
of whole SKUs when a category exceeds agent.max_lines_per_call; uncategorised SKUs are sorted
by ABC class then SKU code and split into agent.uncategorised_batches batches of near-equal
line count."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import pandas as pd

from core.domain.plan_math import line_factors, reference_dp
from worker.context import RunContext
from worker.steps import StepResult, step


@step(name="build_evidence_pack", no=7,
      reads=["history", "classes", "base", "factors", "signals"],
      writes=["evidence_pack", "reference", "agent_batches"],
      config_keys=["agent.show_reference", "history.months", "agent.batch_by", "agent.max_lines_per_call",
                   "agent.uncategorised_batches"], version="1.1")
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
        cat_source = grp["category_source"].iloc[0] if "category_source" in grp.columns else "template"
        pack = {
            "sku": sku, "name": grp["item_name"].iloc[0],
            "plan_category": grp["plan_category"].iloc[0] if cat_source != "none" else None,
            "category_source": cat_source,
            "abc": classes["abc"].get(sku), "sb_class": classes["sb_class"].get(sku),
            "target_month": f"{ctx.target_month:%Y-%m-%d}", "as_of": f"{ctx.as_of:%Y-%m-%d}",
            "horizon_months": ctx.horizon, "lines": lines, "flags": sorted(sku_flags),
        }
        text = json.dumps(pack, sort_keys=True, default=str)
        packs.append({"sku_code": sku, "pack_json": text, "pack_hash": hashlib.sha256(text.encode()).hexdigest(),
                      "n_lines": len(lines), "plan_category": pack["plan_category"],
                      "category_source": cat_source, "abc": pack["abc"]})
    ev = pd.DataFrame(packs, columns=["sku_code", "pack_json", "pack_hash", "n_lines", "plan_category",
                                      "category_source", "abc"])
    ref_df = pd.DataFrame(refs)
    batches = build_batches(ev, signals, ctx.config)
    total = int(ref_df["reference_dp"].sum()) if len(ref_df) else 0
    return StepResult(rows_in=len(base), rows_out=len(ev),
                      summary=f"{len(ev)} evidence packs, {len(ref_df)} lines in {len(batches)} planning-agent "
                              f"batches; reference DP total {total:,} units",
                      artifacts={"evidence_pack": ev.drop(columns=["plan_category", "category_source", "abc"]),
                                 "reference": ref_df, "agent_batches": batches})


BATCH_COLS = ["batch_key", "category", "sku_codes", "line_count", "category_signals"]


def _category_signals(signals: pd.DataFrame, skus: list[str]) -> dict[str, Any]:
    """Category-level facts for the batch: mean category share and competitor OSA over its lines."""
    if signals.empty:
        return {}
    sig = signals[signals.index.map(lambda lid: lid.split("|", 1)[0] in set(skus))]
    vals: dict[str, list[float]] = {"category_share_now": [], "competitor_osa": []}
    for text in sig["signals"]:
        d = json.loads(text)
        for k in vals:
            v = d.get(k)
            if isinstance(v, (int, float)) and math.isfinite(v):
                vals[k].append(float(v))
    return {k: round(sum(v) / len(v), 4) for k, v in vals.items() if v}


def _pack_whole_skus(items: list[tuple[str, int]], cap: int) -> list[list[tuple[str, int]]]:
    """Greedy fill of whole SKUs up to ``cap`` lines; a SKU over the cap gets its own batch."""
    out: list[list[tuple[str, int]]] = []
    cur: list[tuple[str, int]] = []
    n = 0
    for sku, k in items:
        if cur and n + k > cap:
            out.append(cur)
            cur, n = [], 0
        cur.append((sku, k))
        n += k
    if cur:
        out.append(cur)
    return out


def _split_even(items: list[tuple[str, int]], n_batches: int) -> list[list[tuple[str, int]]]:
    """Split whole SKUs, in order, into ``n_batches`` runs of near-equal line count."""
    total = sum(k for _, k in items)
    n_batches = max(1, min(n_batches, len(items)))
    out: list[list[tuple[str, int]]] = []
    cur: list[tuple[str, int]] = []
    done = 0
    for i, (sku, k) in enumerate(items):
        cur.append((sku, k))
        done += k
        remaining_items = len(items) - i - 1
        remaining_batches = n_batches - len(out) - 1
        target = total * (len(out) + 1) / n_batches
        if remaining_batches > 0 and (done >= target or remaining_items == remaining_batches):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def build_batches(ev: pd.DataFrame, signals: pd.DataFrame, cfg: Any) -> pd.DataFrame:
    if ev.empty:
        return pd.DataFrame(columns=BATCH_COLS)
    cap = int(cfg.get("agent.max_lines_per_call"))
    by = cfg.get("agent.batch_by", "category")
    rows = []

    def add(key: str, category: str | None, items: list[tuple[str, int]]) -> None:
        skus = [s for s, _ in items]
        rows.append({"batch_key": key, "category": category, "sku_codes": json.dumps(skus),
                     "line_count": sum(k for _, k in items),
                     "category_signals": json.dumps(_category_signals(signals, skus), sort_keys=True)})

    if by == "sku":
        for r in ev.sort_values("sku_code").itertuples():
            add(f"sku:{r.sku_code}", r.plan_category, [(r.sku_code, int(r.n_lines))])
        return pd.DataFrame(rows, columns=BATCH_COLS)

    cat = ev[ev["category_source"] != "none"]
    for name, grp in cat.sort_values(["plan_category", "sku_code"]).groupby("plan_category", sort=True):
        items = [(r.sku_code, int(r.n_lines)) for r in grp.itertuples()]
        for i, part in enumerate(_pack_whole_skus(items, cap)):
            add(f"cat:{name}#{i}", str(name), part)

    unc = ev[ev["category_source"] == "none"].copy()
    if not unc.empty:
        unc["abc_sort"] = unc["abc"].fillna("Z")
        items = [(r.sku_code, int(r.n_lines)) for r in unc.sort_values(["abc_sort", "sku_code"]).itertuples()]
        total = sum(k for _, k in items)
        n = max(int(cfg.get("agent.uncategorised_batches")), math.ceil(total / cap))
        for i, part in enumerate(_split_even(items, n)):
            add(f"uncat#{i}", None, part)
    return pd.DataFrame(rows, columns=BATCH_COLS)
