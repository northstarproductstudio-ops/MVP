"""Import: tag the file type, propose a two-tier field mapping, and (after the user saves)
materialise typed core rows. Pure functions over a local file; the API/worker persist."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from core.domain import file_types
from core.domain.adapters import REGISTRY, get_adapter
from core.domain.adapters.base import SourceSheet, TransformResult
from core.domain.file_reader import RawSheet, infer_type, norm_key, preview, read_sheet
from core.domain.variables import catalogue, compatible, display_name, hard_variables, humanize
from core.models.imports import ImportProposal, NewVariable, ProposedField, SheetChoice
from core.plan_config import PlanConfig

# (file_name, sheets, unresolved fields, detection, saved) -> agent answer dict
ImportAgentFn = Callable[[dict[str, Any]], dict[str, Any] | None]


@dataclass
class ParsedFile:
    sheets: list[RawSheet]
    sources: list[SourceSheet]
    proposal: ImportProposal

    @property
    def rows_read(self) -> int:
        return sum(len(s.frame) for s in self.sources)


def _samples(s: pd.Series, n: int = 3) -> list[Any]:
    out: list[Any] = []
    for v in s.tolist():
        if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
            continue
        v = v.isoformat()[:10] if hasattr(v, "isoformat") else v
        if v not in out:
            out.append(v if isinstance(v, (int, float, str, bool)) else str(v))
        if len(out) >= n:
            break
    return out


def _blank_pct(s: pd.Series) -> float:
    if len(s) == 0:
        return 1.0
    blank = s.isna() | s.astype("string").str.strip().isin(["", "-"])
    return round(float(blank.mean()), 4)


def parse_file(path: Path, file_name: str, cfg: PlanConfig,
               saved: dict[str, list[dict[str, Any]]] | None = None,
               agent: ImportAgentFn | None = None,
               forced_type: str | None = None) -> ParsedFile:
    """Detect type, read sheets, propose mappings. Stores nothing."""
    sheets = preview(path, n=int(cfg.get("import.sample_rows_for_agent", 30)))
    det = file_types.detect(
        sheets, file_name, accept=cfg.get("import.detect_accept"), tie=cfg.get("import.detect_tie"),
        custom_below=cfg.get("import.detect_custom_below"),
        rows_to_try=cfg.get("import.header_rows_to_try"))
    file_type, score, source, ask, question = det.file_type, det.score, "adapter", False, None
    if forced_type:
        file_type, score, source = forced_type, 1.0, "user"
    elif not det.decided:
        ask = True
        question = (f"Is {file_name} a {REGISTRY.get(det.file_type, REGISTRY['custom']).name} file? "
                    f"({det.reason})")

    adapter = get_adapter(file_type)
    sources = adapter.read(path, sheets, cfg.get("import.header_rows_to_try")) if file_type != "custom" else []
    if file_type == "custom" or not sources:
        sh = sheets[0]
        hdr = max(range(1, min(5, len(sh.preview)) + 1),
                  key=lambda r: sum(1 for v in sh.preview[r - 1] if v not in (None, "")), default=1)
        sources = [SourceSheet(sh.name, hdr, read_sheet(path, sh.name, hdr))]

    # an undecided field saved earlier is not a mapping; it is proposed again
    saved_rows = [r for r in (saved or {}).get(file_type, []) if r.get("state") != "needs_decision"]
    saved_map = {(r["source_sheet"], norm_key(r["source_field"])): r for r in saved_rows}
    saved_any_sheet = {norm_key(r["source_field"]): r for r in saved_rows}
    cat = catalogue()
    fields: list[ProposedField] = []
    used_tier1: set[str] = set()
    for src in sources:
        sheet_used: set[str] = set()
        cols = [c for c in src.frame.columns if isinstance(c, str)]
        known = adapter.mapping_for(cols) if file_type != "custom" else {}
        tier2 = adapter.tier2_for(cols) if file_type != "custom" else {}
        for col in cols:
            series = src.frame[col]
            pf = ProposedField(sheet=src.sheet, source_field=col, sample_values=_samples(series),
                               inferred_type=infer_type(series.head(200).tolist()),
                               blank_pct=_blank_pct(series))
            sv = saved_map.get((src.sheet, norm_key(col))) or saved_any_sheet.get(norm_key(col))
            if sv is not None:
                pf.variable, pf.category = sv.get("variable"), sv.get("category")
                pf.state = "recognised" if sv.get("variable") else "not_stored"
                pf.confidence, pf.note = 1.0, "saved mapping"
            elif col in known:
                v = cat[known[col]]
                pf.variable, pf.category, pf.tier = v.name, v.category, 1
                pf.state, pf.confidence = "recognised", 1.0
            elif col in tier2:
                name, category = tier2[col]
                pf.variable, pf.category, pf.tier = name, category, 2
                pf.new_variable = NewVariable(name=name, category=category,
                                              data_type=pf.inferred_type or "text", display_name=humanize(name))
                pf.state = "proposed" if category in {c.category for c in cat.values()} else "new_category"
                pf.confidence = 0.8
            elif pf.inferred_type == "empty":
                pf.state, pf.confidence, pf.note = "not_stored", 0.9, "empty column"
            else:
                pf.state = "needs_decision"
            if pf.variable and (pf.tier == 1 or pf.variable in cat):
                if pf.variable in sheet_used:
                    pf.variable, pf.state, pf.note = None, "needs_decision", "another field already maps here"
                else:
                    sheet_used.add(pf.variable)
                    used_tier1.add(pf.variable)
            fields.append(pf)

    proposal = ImportProposal(file_type=file_type, type_confidence=score, type_source=source,
                              ask_user=ask, question=question,
                              sheets=[SheetChoice(sheet=s.sheet, header_row=s.header_row) for s in sources],
                              fields=fields)

    unresolved = [f for f in fields if f.state == "needs_decision"]
    if agent is not None and (unresolved or ask):
        answer = agent({
            "file_name": file_name,
            "sheets": [{"sheet": s.name, "rows": [[_cell(v) for v in r] for r in s.preview]} for s in sheets],
            "detection": {"file_type": det.file_type, "score": det.score, "reason": det.reason,
                          "scores": [t.__dict__ for t in det.scores[:4]], "hint": det.hint},
            "fields": [{"sheet": f.sheet, "source_field": f.source_field, "samples": f.sample_values,
                        "inferred_type": f.inferred_type} for f in unresolved],
            "used_tier1": sorted(used_tier1),
        })
        if answer:
            proposal = merge_agent_answer(proposal, answer, used_tier1, decided=det.decided or bool(forced_type))
    elif agent is None:
        # Phase 1 behaviour: adapters only. Unknown columns of a typed file are not stored
        # unless the user maps them; custom files keep needs_decision.
        if file_type != "custom":
            for f in proposal.fields:
                if f.state == "needs_decision" and f.note is None:
                    f.state, f.note = "not_stored", "no adapter mapping; map by hand to keep it"

    group = REGISTRY.get(file_type, REGISTRY["custom"]).group
    mapped_vars = {f.variable for f in proposal.fields if f.variable}
    for hv in hard_variables(group) if group == "sellin" else []:
        if hv not in mapped_vars:
            proposal.warnings.append(f"Required field {display_name(hv)} is not mapped; a plan cannot run until it is")
    return ParsedFile(sheets=sheets, sources=sources, proposal=proposal)


def _cell(v: Any) -> Any:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()[:19]
    if isinstance(v, (int, float, bool)):
        return v
    return str(v)[:80]


def merge_agent_answer(p: ImportProposal, ans: dict[str, Any], used_tier1: set[str],
                       decided: bool) -> ImportProposal:
    """Merge the import agent's answer under the agent rules: tier-1 only when the type
    fits, no two fields on one tier-1 variable, never a guessed value."""
    cat = catalogue()
    if not decided and ans.get("file_type") in REGISTRY:
        p.file_type = ans["file_type"]
        p.type_confidence = float(ans.get("type_confidence") or 0)
        p.type_source = "agent"
        p.ask_user = bool(ans.get("ask_user"))
        p.question = ans.get("question") if p.ask_user else None
    by_key = {(f.sheet, f.source_field): f for f in p.fields}
    for a in ans.get("fields") or []:
        f = by_key.get((a.get("sheet"), a.get("source_field")))
        if f is None or f.state != "needs_decision":
            continue
        state = a.get("state")
        var = a.get("variable")
        tier = a.get("tier")
        f.confidence = float(a.get("confidence") or 0)
        if state == "not_stored":
            f.state, f.variable, f.category = "not_stored", None, None
            continue
        if not var:
            continue
        if tier == 1 or var in cat:
            v = cat.get(var)
            if v is None or v.tier != 1 or not compatible(v, f.inferred_type or "text") or var in used_tier1:
                f.state, f.variable = "needs_decision", None
                f.note = "agent proposal rejected (type mismatch or duplicate)"
                continue
            used_tier1.add(var)
            f.variable, f.category, f.tier, f.state = var, v.category, 1, "proposed"
        else:
            nv = a.get("new_variable") or {"name": var, "category": a.get("category") or "Other",
                                           "data_type": f.inferred_type or "text"}
            nv = {**nv, "display_name": (nv.get("display_name") or "").strip() or humanize(nv["name"])}
            f.variable, f.tier = nv["name"], 2
            f.category = nv.get("category") or a.get("category")
            f.new_variable = NewVariable(**nv)
            known_cats = {c.category for c in cat.values()}
            f.state = "proposed" if f.category in known_cats else "new_category"
    return p


def mapping_from_rows(rows: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    return {(r["source_sheet"], r["source_field"]): r["variable"] for r in rows
            if r.get("variable") and r.get("state") not in ("not_stored", "needs_decision")}


def materialize(parsed_sources: list[SourceSheet], file_type: str,
                mapping: dict[tuple[str, str], str], ctx: dict[str, Any]) -> TransformResult:
    adapter = get_adapter(file_type)
    return adapter.transform(parsed_sources, mapping, ctx)


def import_file(path: Path, file_name: str, cfg: PlanConfig, dataset_id: str = "local",
                own_brand_names: list[str] | None = None, forced_type: str | None = None
                ) -> tuple[ParsedFile, TransformResult]:
    """Offline helper: parse and materialise with the proposed mapping as-is."""
    parsed = parse_file(path, file_name, cfg, forced_type=forced_type)
    rows = [{"source_sheet": f.sheet, "source_field": f.source_field, "variable": f.variable,
             "state": f.state} for f in parsed.proposal.fields]
    res = materialize(parsed.sources, parsed.proposal.file_type, mapping_from_rows(rows), {
        "dataset_id": dataset_id, "file_name": file_name,
        "own_brand_names": own_brand_names if own_brand_names is not None else cfg.get("brand.own_names"),
    })
    return parsed, res
