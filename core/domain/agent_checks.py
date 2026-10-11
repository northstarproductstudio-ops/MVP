"""Step 9 checks on planning-agent output, per line, in order:

1. valid schema; every pack line present exactly once
2. every applied factor is a pack factor, an ``agent:`` factor or ``judgment``
3. agent factor values within agent.factor_bounds; judgment within ±agent.judgment_cap;
   at most one judgment; every agent factor has a reason
4. recomputed arithmetic matches final_dp within agent.arith_tolerance
5. final_dp within agent.band of the reference
6. a gap over agent.explain_threshold names a pack factor or signal in the reason
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from core.domain.plan_math import compute_dp
from core.models.planning import AgentOutput


def resolve_applied(pack_line: dict[str, Any], applied: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kinds = {f["name"]: f["kind"] for f in pack_line["factors"]}
    out = []
    for a in applied:
        kind = kinds.get(a["name"], "multiplier")
        out.append({"name": a["name"], "kind": kind, "value": float(a["value"]), "reason": a.get("reason")})
    return out


def check_output(pack: dict[str, Any], raw: Any, cfg: Any) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Return (valid lines by line_id, failed checks by line_id; key '*' for pack-level)."""
    failed: dict[str, list[str]] = {}
    try:
        out = AgentOutput.model_validate(raw)
    except ValidationError as exc:
        return {}, {"*": [f"schema: {exc.errors()[0]['msg']}"]}
    pack_lines = {ln["line_id"]: ln for ln in pack["lines"]}
    seen: dict[str, int] = {}
    for ln in out.lines:
        seen[ln.line_id] = seen.get(ln.line_id, 0) + 1
    missing = [lid for lid in pack_lines if lid not in seen]
    extra = [lid for lid in seen if lid not in pack_lines]
    dup = [lid for lid, n in seen.items() if n > 1]
    if missing or extra or dup:
        msg = []
        if missing:
            msg.append(f"missing lines {missing[:5]}")
        if extra:
            msg.append(f"unknown lines {extra[:5]}")
        if dup:
            msg.append(f"duplicate lines {dup[:5]}")
        failed["*"] = ["schema: " + "; ".join(msg)]

    lo_b, hi_b = cfg.get("agent.factor_bounds")
    jcap = float(cfg.get("agent.judgment_cap"))
    tol = cfg.get("agent.arith_tolerance")
    band_lo, band_hi = cfg.get("agent.band")
    explain = float(cfg.get("agent.explain_threshold"))
    good: dict[str, dict[str, Any]] = {}
    for ln in out.lines:
        pl = pack_lines.get(ln.line_id)
        if pl is None or seen[ln.line_id] > 1:
            continue
        errs: list[str] = []
        names = {f["name"] for f in pl["factors"]}
        applied = [a.model_dump() for a in ln.applied_factors]
        judg = [a for a in applied if a["name"] == "judgment"]
        for a in applied:
            n = a["name"]
            if not (n in names or n.startswith("agent:") or n == "judgment"):
                errs.append(f"unknown factor {n}")
            if n.startswith("agent:"):
                if not (lo_b <= a["value"] <= hi_b):
                    errs.append(f"{n} value {a['value']} outside bounds [{lo_b}, {hi_b}]")
                if not (a.get("reason") or "").strip():
                    errs.append(f"{n} has no reason")
        if len(judg) > 1:
            errs.append("more than one judgment factor")
        for j in judg:
            if abs(j["value"]) > jcap:
                errs.append(f"judgment {j['value']} outside ±{jcap}")
        resolved = resolve_applied(pl, applied)
        recomputed = max(0.0, compute_dp(pl["base"], resolved))
        allowed = max(float(tol["units"]), float(tol["pct"]) * abs(recomputed))
        if abs(recomputed - ln.final_dp) > allowed:
            errs.append(f"arithmetic: factors give {recomputed:.1f} but final_dp is {ln.final_dp}")
        ref = pl.get("reference_dp")
        if ref is None:
            ref = round(compute_dp(pl["base"], [dict(f) for f in pl["factors"]]))
        if ref > 0:
            if not (band_lo * ref <= ln.final_dp <= band_hi * ref):
                errs.append(f"final_dp {ln.final_dp} outside band [{band_lo}×, {band_hi}×] of reference {ref}")
            gap = abs(ln.final_dp - ref) / ref
            if gap > explain:
                vocab = names | set(pl.get("signals", {}).keys()) | {a["name"] for a in applied}
                vocab |= {n.split(":", 1)[1] for n in vocab if ":" in n}
                text = (ln.reason or "").lower()
                if not any(v.lower().replace("_", " ") in text.replace("_", " ") for v in vocab):
                    errs.append(f"gap {gap:.0%} vs reference not explained by a factor or signal")
        elif ln.final_dp > float(tol["units"]):
            errs.append(f"final_dp {ln.final_dp} where reference is 0")
        if errs:
            failed[ln.line_id] = errs
        else:
            good[ln.line_id] = {"line": ln.model_dump(), "applied": resolved, "recomputed": recomputed}
    return good, failed


# --------------------------------------------------------------------------- batches (prompt v2)

def expand_batch_output(pack: dict[str, Any], raw: Any) -> tuple[dict[str, Any] | None, dict[str, list[str]]]:
    """Turn a v2 batch answer into the v1 per-SKU shape the checks above expect.

    Keeps only this pack's lines and expands category factors into each line's applied
    factors: ``value: null`` takes the category value and reason; a line may override a
    category factor only with its own reason. Returns (``{"lines": [...]}`` or None, failed).
    """
    from core.models.planning import BatchOutput

    if isinstance(raw, dict) and "category_factors" not in raw and "lines" in raw:
        raw = {**raw, "category_factors": []}  # tolerate a v1-shaped answer
    try:
        out = BatchOutput.model_validate(raw)
    except ValidationError as exc:
        return None, {"*": [f"schema: {exc.errors()[0]['msg']}"]}
    failed: dict[str, list[str]] = {}
    cat = {}
    for cf in out.category_factors:
        if not cf.name.startswith("agent:"):
            failed.setdefault("*", []).append(f"category factor {cf.name} must be named agent:<short_name>")
            continue
        if not cf.reason.strip():
            failed.setdefault("*", []).append(f"category factor {cf.name} has no reason")
        cat[cf.name] = cf
    mine = {ln["line_id"] for ln in pack["lines"]}
    lines = []
    for ln in out.lines:
        if ln.line_id not in mine:
            continue
        errs: list[str] = []
        applied = []
        for a in ln.applied_factors:
            cf = cat.get(a.name)
            if a.value is None:
                if cf is None:
                    errs.append(f"{a.name} has no value and is not a category factor")
                    continue
                applied.append({"name": a.name, "value": cf.value, "reason": cf.reason})
            else:
                if cf is not None and abs(a.value - cf.value) > 1e-9 and not (a.reason or "").strip():
                    errs.append(f"{a.name} overrides the category value without a reason")
                reason = a.reason if (a.reason or "").strip() else (cf.reason if cf is not None else a.reason)
                applied.append({"name": a.name, "value": float(a.value), "reason": reason})
        if errs:
            failed[ln.line_id] = errs
        lines.append({"line_id": ln.line_id, "final_dp": ln.final_dp, "applied_factors": applied,
                      "reason": ln.reason, "confidence": ln.confidence, "flag_for_review": ln.flag_for_review})
    return {"lines": lines}, failed


def check_batch_output(pack: dict[str, Any], raw: Any, cfg: Any
                       ) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Expand a batch answer for one SKU pack, then run the per-line checks."""
    expanded, failed = expand_batch_output(pack, raw)
    if expanded is None:
        return {}, failed
    good, more = check_output(pack, expanded, cfg)
    for lid, errs in more.items():
        failed.setdefault(lid, []).extend(errs)
    for lid in [k for k in failed if k != "*"]:
        good.pop(lid, None)
    if any("category factor" in e for e in failed.get("*", [])):
        good = {}  # a bad category factor taints every line that could use it
    return good, failed
