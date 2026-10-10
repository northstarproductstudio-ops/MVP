"""Produce a demand plan from local source files, without the app.

    python scripts/plan_offline.py --data ./sample-data --target 2026-11 --out ./out
    python scripts/plan_offline.py --data ./sample-data --no-crosswalk-gate   # warn instead of fail

Writes plan.xlsx (formatRef layout), reasoning.xlsx, crosswalk_draft.xlsx and a run summary.
The planning agent is off unless --agent is given (then OPENROUTER_API_KEY and LLM_MODEL
must be set).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from core.domain.reports import current_lines, export_plan_xlsx, export_reasoning_xlsx, reasoning_rows  # noqa: E402
from core.plan_config import PlanConfig  # noqa: E402
from worker.local import import_folder, run_local  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True, type=Path)
    p.add_argument("--target", default=None, help="YYYY-MM; default = month after the data's as-of month")
    p.add_argument("--out", default=Path("out"), type=Path)
    p.add_argument("--agent", action="store_true", help="enable the planning agent (needs OpenRouter)")
    p.add_argument("--no-crosswalk-gate", action="store_true",
                   help="warn instead of failing when sold SKUs have no crosswalk row")
    p.add_argument("--crosswalk", type=Path, help="reviewed crosswalk xlsx/csv to apply")
    p.add_argument("--platforms", help="comma-separated platforms to plan (default: all)")
    args = p.parse_args()

    overrides = {"agent.enabled": bool(args.agent)}
    if args.no_crosswalk_gate:
        overrides["crosswalk.hard_fail_unmapped"] = False
    cfg = PlanConfig.load().with_overrides(overrides)
    files = sorted(f for f in args.data.iterdir() if f.suffix.lower() in (".csv", ".xlsx", ".xls")
                   and "formatref" not in f.name.lower())
    cw = None
    if args.crosswalk:
        cw = pd.read_excel(args.crosswalk) if args.crosswalk.suffix != ".csv" else pd.read_csv(args.crosswalk)
        if "confirm" in cw:
            ok = cw["confirm"].astype(str).str.strip().str.lower().isin(["true", "yes", "y", "1"])
            cw.loc[ok, "match_method"] = "manual"
            if "proposed_sku" in cw:
                cw.loc[ok & cw["sku_code"].isna(), "sku_code"] = cw.loc[ok & cw["sku_code"].isna(), "proposed_sku"]
            cw = cw[ok | cw["match_method"].isin(["auto", "ean"])]

    t0 = time.monotonic()
    snap, warns = import_folder(files, cfg, cw)
    t_import = time.monotonic() - t0
    llm = None
    if args.agent:
        from core.llm import LLMClient

        llm = LLMClient(api_key=os.environ["OPENROUTER_API_KEY"], model=os.environ["LLM_MODEL"])
    args.out.mkdir(parents=True, exist_ok=True)
    cwd = snap.tables["sku_crosswalk"]
    cwd.assign(confirm=cwd["match_method"].isin(["ean", "auto", "manual"])).to_excel(
        args.out / "crosswalk_draft.xlsx", index=False)
    t1 = time.monotonic()
    try:
        scope = {"platforms": args.platforms.split(",")} if args.platforms else None
        run = run_local(snap, cfg, args.target, llm=llm, scope=scope)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "InputError":
            print(str(exc))
            print(f"\nReview {args.out / 'crosswalk_draft.xlsx'} (set confirm, fix proposed_sku), then rerun with "
                  "--crosswalk <file>, or use --no-crosswalk-gate to plan without those platform factors.")
            return 2
        raise
    t_run = time.monotonic() - t1

    dp = run.write("dp_output")
    lines = current_lines(dp, None)
    lines["share_basis"] = lines["line_id"].map(run.ctx.artifacts["base"].set_index("line_id")["share_basis"])
    hist = run.ctx.artifacts["history"]
    actuals = hist[hist["month"].isin(run.ctx.history_months())]
    factors = pd.concat([run.ctx.artifacts["factors"], run.write("factor")], ignore_index=True)
    meta = {"target_month": f"{run.ctx.target_month:%Y-%m-%d}", "version": 0, "run_id": run.ctx.run_id}
    sm = snap.tables["sellin_line"]
    mrp = sm.dropna(subset=["mrp"]).groupby("sku_code")["mrp"].last().to_dict()
    (args.out / "plan.xlsx").write_bytes(export_plan_xlsx(
        ROOT / "templates" / "formatRef.xlsx", lines, actuals, factors, meta, cfg.cities, mrp))
    (args.out / "reasoning.xlsx").write_bytes(export_reasoning_xlsx(reasoning_rows(lines, 0), meta))
    report = json.loads(run.write("report")["payload"].iloc[0])
    summary = {
        "as_of": f"{run.ctx.as_of:%Y-%m-%d}", "target_month": meta["target_month"],
        "import_seconds": round(t_import, 1), "pipeline_seconds": round(t_run, 1),
        "steps": {n: {"status": r.status, "summary": r.summary, "warnings": r.warnings,
                      "seconds": round(run.log.durations.get(n, 0), 2)} for n, r in run.log.results.items()},
        "snapshot_warnings": snap.warnings, "import_warnings": warns, "report": report,
    }
    (args.out / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps({k: summary[k] for k in ("as_of", "target_month", "import_seconds", "pipeline_seconds")}))
    for n, s in summary["steps"].items():
        print(f"  {n:>2} {s['status']:<9} {s['seconds']:>6}s  {s['summary']}")
        for w in s["warnings"]:
            print(f"       ! {w}")
    print("\n" + report["overview"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
