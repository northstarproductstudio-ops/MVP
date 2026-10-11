"""Gap Fix Spec unit tests: 7, 8, 8b, 9, 10 (pure rule), 11."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from core.domain.readiness import DatasetState, coverage, evaluate
from core.domain.variables import catalogue, display_name

# --------------------------------------------------------------------------- Gap 11


def test_every_catalogue_variable_has_a_display_name_and_help():
    for v in catalogue().values():
        assert v.display_name.strip(), v.name
        assert v.display_name != v.name, v.name  # planners never see the raw name
        assert v.help, v.name


def test_spec_display_names():
    expected = {"sku_code": "SKU code", "order_date": "Order date", "so_status": "Order status",
                "customer_name": "Customer", "ship_city": "Shipping city", "qty_invoiced": "Quantity invoiced",
                "qty_cancelled": "Quantity cancelled", "plan_category": "Product category",
                "po_qty_ordered": "PO quantity ordered", "po_qty_received": "PO quantity received",
                "soh_units": "Stock on hand", "run_rate": "Daily run rate", "osa_pct": "On-shelf availability %",
                "sales_value_sp": "Market sales value"}
    assert {k: display_name(k) for k in expected} == expected
    assert display_name("expected_shipment_date") == "Expected shipment date"  # tier-2 fallback


def test_plan_category_can_be_mapped_from_sales_po_and_custom_files():
    assert set(catalogue()["plan_category"].file_groups) == {"sku_master", "sellin", "po", "custom"}
    assert catalogue()["plan_category"].level == "soft"


# --------------------------------------------------------------------------- Gap 10 (pure rule)

HARD = ["sku_code", "order_date", "so_status", "customer_name", "ship_city", "qty_invoiced"]


def _sales(status: str = "ready", states: dict[str, str] | None = None) -> DatasetState:
    states = states if states is not None else dict.fromkeys(HARD, "confirmed")
    return DatasetState(id="d", file_name="SOs.xlsx", file_type="erp_sales_orders", status=status,
                        fields=[(k.upper(), k, st) for k, st in states.items()])


def test_readiness_rule():
    assert evaluate([]).code == "no_sales_file"
    assert evaluate([_sales()]).plan_ready
    r = evaluate([_sales(states={**dict.fromkeys(HARD, "confirmed"), "order_date": "proposed"})])
    assert not r.plan_ready and r.blocking == ["order_date"] and r.as_dict()["blocking_display"] == ["Order date"]
    assert not evaluate([_sales(status="parsed")]).plan_ready  # not saved yet
    by = {v["name"]: v for v in evaluate([_sales(status="parsed")]).variables}
    assert by["order_date"]["status"] == "proposed" and by["order_date"]["needed_for"] == "plan"
    by = {v["name"]: v for v in evaluate([_sales()]).variables}
    assert by["order_date"]["status"] == "mapped" and by["order_date"]["mapped_from"] == "SOs.xlsx · ORDER_DATE"
    assert by["soh_units"]["status"] == "missing" and by["soh_units"]["needed_for"] == "factor:stock_cover"
    assert by["po_number"]["needed_for"] == "join"


# --------------------------------------------------------------------------- Gap 8


def test_coverage_marks_enhancers_missing_and_factors_off():
    cov = {c["file_group"]: c for c in coverage([_sales()])}
    assert cov["sellin"]["present"] and cov["sellin"]["plan_role"] == "essential"
    assert not cov["po"]["present"] and cov["po"]["factors_off"] == ["fill_rate"]
    assert cov["market"]["plan_role"] == "enhancer" and "sell_through" in cov["market"]["factors_off"]
    assert cov["stock"]["missing_means"]


def _con(**tables: pd.DataFrame) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for name, df in tables.items():
        con.register(name, df)
    return con


def test_known_query_on_missing_file_says_source_missing():
    from core.domain.known_queries import SourceMissing, run_known
    from core.domain.tables import empty_frame

    con = _con(po_line=empty_frame("po_line"), market_daily=empty_frame("market_daily"),
               stock_snapshot=empty_frame("stock_snapshot"), sellin_line=empty_frame("sellin_line"),
               sku_master=empty_frame("sku_master"))
    with pytest.raises(SourceMissing) as exc:
        run_known(con, "fill_rate", {})
    assert exc.value.as_dict()["error_code"] == "source_missing"
    assert exc.value.as_dict()["file_type"] == "po"
    with pytest.raises(SourceMissing):
        run_known(con, "market_position", {})
    with pytest.raises(SourceMissing):
        run_known(con, "top_movers", {"metric": "secondary"})


def test_fenced_lookup_flags_empty_tables():
    from core.domain.fenced import run_fenced
    from core.domain.tables import empty_frame

    con = _con(po_line=empty_frame("po_line"))
    res = run_fenced(con, "select platform, count(*) n from po_line group by 1", 50, 5)
    assert res.empty_tables == ["po_line"]


def test_missing_file_action_for_precondition_and_source_errors():
    from api.services.ask_agent import missing_file

    assert missing_file({"error_code": "no_sales_history"})["file_group"] == "sellin"
    assert missing_file({"error_code": "sales_fields_unmapped"})["action"] == "map"
    assert missing_file({"error_code": "source_missing", "file_type": "market"})["file_group"] == "market"
    assert missing_file({"rows": []}) is None


def test_precondition_error_is_structured():
    from api.services.runs import PreconditionError

    e = PreconditionError("no history", code="scope_has_no_history", items=["TSA Tutti Frutti 100 g"])
    assert e.as_dict() == {"error_code": "scope_has_no_history", "message": "no history",
                           "items": ["TSA Tutti Frutti 100 g"]}


def test_suggestions_only_from_present_file_types():
    from api.services.suggestions import candidate_facts, template_questions
    from core.domain.tables import empty_frame

    md = pd.DataFrame({"platform": ["blinkit"], "sku_code": ["A"], "is_own_brand": [True], "osa_pct": [10.0],
                       "market_date": [pd.Timestamp("2026-09-30")]})
    con = _con(sellin_line=empty_frame("sellin_line"), market_daily=md, po_line=empty_frame("po_line"),
               stock_snapshot=empty_frame("stock_snapshot"))
    facts = candidate_facts(con, False, 0.15, 50, 0.95)
    assert [f["fact"] for f in facts] == ["low_osa"]
    q = template_questions(facts, False, 6, has_sales=False)
    assert q["plan"] == [] and q["data"] and all("sold" not in x for x in q["data"])


# --------------------------------------------------------------------------- Gap 8b


def test_factor_source_registry():
    from core.domain.factor_sources import source_label

    assert source_label("base") == source_label("trend") == "Sales history (Zoho sales orders)"
    assert source_label("sell_through") == "Market data (Dcluttr) vs sales history"
    assert source_label("fill_rate") == "Platform purchase orders"
    assert source_label("stock_cover") == "Platform stock (Blinkit DOH)"
    assert source_label("agent:diwali") == "Planning agent judgment (no data file)"
    assert source_label("user_edit", "user") == "Planner adjustment"


def test_reasoning_rows_name_sources(local_run):
    from core.domain.reports import current_lines, reasoning_rows

    dp = local_run.write("dp_output")
    rows = reasoning_rows(current_lines(dp, None), 0)
    assert rows["sources_used"].notna().all()
    assert rows["sources_used"].str.startswith("Sales history").all()
    assert rows["factors_off"].str.contains("seasonal").all()
    factors = local_run.write("factor")
    assert factors["source_label"].notna().all() and (factors["source_label"] != "").all()


def test_overview_ends_with_sources_line(local_run):
    rep = json.loads(local_run.write("report")["payload"].iloc[0])
    assert rep["sources"]["used"][0].startswith("Sales history")
    assert "Built from Sales history" in rep["overview"]  # template overview (no model in local runs)
    from core.domain.reports import sources_sentence

    assert sources_sentence({"used": ["Sales history Jul–Sep 2026"], "missing": ["no PO file, so fill rate is off"]}) \
        == "Built from Sales history Jul–Sep 2026; not available: no PO file, so fill rate is off."


# --------------------------------------------------------------------------- Gap 7 (step 1)


def test_skus_without_history_are_listed_not_planned(snapshot, cfg):
    from worker.local import run_local

    tables = dict(snapshot.tables)
    po = tables["po_line"].copy()
    extra = po.iloc[:1].copy()
    extra["sku_code"] = "NOHIST01"
    extra["po_number"] = "PX-NOHIST"
    tables["po_line"] = pd.concat([po, extra], ignore_index=True)
    snap = type(snapshot)(**{**snapshot.__dict__, "tables": tables})
    run = run_local(snap, cfg.with_overrides({"agent.enabled": False, "crosswalk.hard_fail_unmapped": False}),
                    "2026-11")
    assert "NOHIST01" not in set(run.write("dp_output")["sku_code"])  # never planned as 0
    rep = json.loads(run.write("report")["payload"].iloc[0])
    assert rep["not_planned"] == ["NOHIST01"]
    assert any(w.startswith("no_history_skus: 1 SKUs") for w in run.log.warnings)


def test_no_usable_sales_history_stops_the_run(snapshot, cfg):
    from worker.local import run_local
    from worker.steps import all_steps

    all_steps()
    import importlib

    v = importlib.import_module("worker.steps.01_validate_inputs")
    tables = dict(snapshot.tables)
    s = tables["sellin_line"].copy()
    s["status"] = "void"
    tables["sellin_line"] = s
    snap = type(snapshot)(**{**snapshot.__dict__, "tables": tables})
    with pytest.raises(v.InputError) as exc:
        run_local(snap, cfg.with_overrides({"agent.enabled": False, "crosswalk.hard_fail_unmapped": False}), "2026-11")
    assert exc.value.code == "no_sales_history"


# --------------------------------------------------------------------------- Gap 9


def _with_extra_column(src: Path, dst: Path) -> Path:
    with open(src, encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    rows[0].append("Vendor Remarks")
    for r in rows[1:]:
        r.append("call before dispatch")
    with open(dst, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return dst


def test_saved_correction_is_reused_with_zero_agent_calls(sample_dir: Path, cfg, tmp_path: Path):
    from core.domain.importing import parse_file

    src = next(p for p in sample_dir.iterdir() if p.name.startswith("Zepto PO"))
    f1 = _with_extra_column(src, tmp_path / "Zepto PO Export - Aug-2026.csv")
    calls: list[dict[str, Any]] = []

    def agent(payload: dict[str, Any]) -> dict[str, Any] | None:
        calls.append(payload)
        return None  # the agent cannot place it either

    p1 = parse_file(f1, f1.name, cfg, agent=agent).proposal
    assert len(calls) == 1  # called once for the one unresolved field
    remarks = next(f for f in p1.fields if f.source_field == "Vendor Remarks")
    assert remarks.state == "needs_decision"

    # the user maps it by hand and corrects a recognised field; both are saved
    saved = {p1.file_type: [
        {"source_sheet": f.sheet, "source_field": f.source_field,
         "variable": "dispatch_note" if f.source_field == "Vendor Remarks" else f.variable,
         "category": "Purchase orders", "state": "custom" if f.source_field == "Vendor Remarks" else "confirmed"}
        for f in p1.fields if f.variable or f.source_field == "Vendor Remarks"]}

    f2 = shutil.copy(f1, tmp_path / "zepto_po_sept_renamed.csv")
    p2 = parse_file(Path(f2), "zepto_po_sept_renamed.csv", cfg, saved=saved, agent=agent).proposal
    again = next(f for f in p2.fields if f.source_field == "Vendor Remarks")
    assert again.state == "recognised" and again.variable == "dispatch_note" and again.note == "saved mapping"
    assert len(calls) == 1  # no new import-agent call


def test_saved_undecided_field_is_proposed_again(sample_dir: Path, cfg, tmp_path: Path):
    from core.domain.importing import parse_file

    src = next(p for p in sample_dir.iterdir() if p.name.startswith("Zepto PO"))
    f1 = _with_extra_column(src, tmp_path / "z.csv")
    p1 = parse_file(f1, f1.name, cfg).proposal
    saved = {p1.file_type: [{"source_sheet": "csv", "source_field": "Vendor Remarks", "variable": None,
                             "category": None, "state": "needs_decision"}]}
    calls: list[Any] = []
    parse_file(f1, f1.name, cfg, saved=saved, agent=lambda p: calls.append(p) or None)
    assert len(calls) == 1  # an undecided field is not a saved mapping
