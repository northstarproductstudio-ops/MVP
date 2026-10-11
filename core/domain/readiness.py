"""Plan readiness (Gap 10) and source coverage (Gap 8) — pure functions over the session's
datasets and their mappings, shared by the readiness API, ``preconditions()`` and the Ask
agent's workspace summary, so the screen and the run never disagree.

Only sales history is essential, and only for a demand plan; every other file type
enhances the plan. Mapping never blocks Ask.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from core.domain.variables import Variable, catalogue, display_name

# field_mapping / proposal states that count as mapped for the plan gate (Gap 7):
# proposed and new_category are not yet confirmed by the user.
MAPPED_STATES = frozenset({"confirmed", "recognised", "custom"})
GROUP_LABELS = {"sellin": "ERP sales orders (Zoho)", "po": "Platform purchase orders",
                "stock": "Platform stock (Blinkit DOH)", "market": "Market insight (Dcluttr)",
                "sku_master": "SKU master", "custom": "Custom files"}


@lru_cache(maxsize=1)
def file_type_roles() -> dict[str, dict[str, Any]]:
    """file_type key → {group, plan_role, factors, missing_means, questions, name} from the seed library."""
    from core.domain.file_types import library

    return {ft["key"]: {"group": ft.get("group"), "plan_role": ft.get("plan_role", "enhancer"),
                        "factors": ft.get("factors") or [], "missing_means": ft.get("missing_means"),
                        "questions": ft.get("questions"), "name": ft.get("name")} for ft in library()}


def group_roles() -> dict[str, dict[str, Any]]:
    """One entry per file group (the PO types share one)."""
    out: dict[str, dict[str, Any]] = {}
    for key, r in file_type_roles().items():
        g = r["group"]
        if g and g not in out:
            out[g] = {**r, "file_types": [key], "label": GROUP_LABELS.get(g, r["name"])}
        elif g:
            out[g]["file_types"].append(key)
    return out


@dataclass
class DatasetState:
    """One non-removed dataset of the session with its field mappings.

    ``fields``: (source_field, variable, state) from the saved mapping when the file has
    been saved, else from the import proposal."""
    id: str
    file_name: str
    file_type: str | None
    status: str
    rows: int | None = None
    data_from: Any = None
    data_to: Any = None
    fields: list[tuple[str, str | None, str]] = field(default_factory=list)


@dataclass
class Readiness:
    plan_ready: bool
    blocking: list[str]
    variables: list[dict[str, Any]]
    code: str | None = None  # no_sales_file | sales_fields_unmapped | None

    def as_dict(self) -> dict[str, Any]:
        return {"plan_ready": self.plan_ready, "blocking": self.blocking, "code": self.code,
                "blocking_display": [display_name(v) for v in self.blocking], "variables": self.variables}


def _needed_for(v: Variable) -> str:
    if v.level == "hard":
        return "plan"
    if v.level == "key":
        return "join"
    if v.tier == 2 or v.level == "none":
        return "questions"
    f = VAR_FACTOR.get(v.name, FACTOR_OF_GROUP.get(v.file_group or ""))
    return f"factor:{f}" if f else "questions"


# soft field → the factor or feature it switches on when mapped
FACTOR_OF_GROUP = {"po": "fill_rate", "stock": "stock_cover", "market": "sell_through", "sku_master": "netting"}
VAR_FACTOR = {
    "qty_cancelled": "base", "qty_ordered": "base", "item_price": "abc", "mrp": "abc", "item_name": "crosswalk",
    "so_number": None, "reference_no": None, "po_qty_open": "open_po", "platform_item_name": "crosswalk",
    "unit_cost": None, "osa_pct": "osa_signal", "category_share_mrp": "market_ceiling",
    "category_share_sp": "market_ceiling", "discount_pct": "discount_signal", "plan_category": "category_batches",
    "brand": "sell_through", "category": "market_ceiling", "product_name": None, "pack_size": "crosswalk",
    "market_city": None, "stock_city": "stock_cover", "snapshot_date": "stock_cover",
}


def evaluate(datasets: list[DatasetState], extra_variables: list[dict[str, Any]] | None = None) -> Readiness:
    """Readiness of the session's data for a demand plan, plus a status per catalogue variable.

    plan_ready ⇔ at least one *ready* sales-order file has every hard sell-in variable
    mapped with state confirmed, recognised or custom — the exact rule ``preconditions()``
    enforces."""
    cat = catalogue()
    hard = [v.name for v in cat.values() if v.level == "hard" and v.file_group == "sellin"]
    sales_ready = [d for d in datasets if d.file_type == "erp_sales_orders" and d.status == "ready"]
    code: str | None = None
    if not sales_ready:
        blocking = list(hard)
        code = "no_sales_file"
    else:
        best: list[str] | None = None
        for d in sales_ready:
            mapped = {v for _, v, st in d.fields if v and st in MAPPED_STATES}
            miss = [h for h in hard if h not in mapped]
            if best is None or len(miss) < len(best):
                best = miss
        blocking = best or []
        if blocking:
            code = "sales_fields_unmapped"

    # status per variable across every non-removed file
    status: dict[str, tuple[str, str | None]] = {}
    rank = {"mapped": 3, "proposed": 2, "needs_decision": 1, "missing": 0}
    for d in datasets:
        for src, var, st in d.fields:
            if not var:
                continue
            s = ("mapped" if st in MAPPED_STATES and d.status in ("ready", "mapped") else
                 "proposed" if st in ("proposed", "new_category", "recognised", "confirmed", "custom") else
                 "needs_decision" if st == "needs_decision" else None)
            if s is None:
                continue
            cur = status.get(var)
            if cur is None or rank[s] > rank[cur[0]]:
                status[var] = (s, f"{d.file_name} · {src}")
    rows = []
    for v in cat.values():
        st, frm = status.get(v.name, ("missing", None))
        rows.append({"name": v.name, "display_name": v.label, "description": v.help or v.definition,
                     "file_group": v.file_group, "needed_for": _needed_for(v), "status": st,
                     "mapped_from": frm if st == "mapped" else None,
                     "proposed_from": frm if st != "mapped" else None})
    for ev in extra_variables or []:  # tier-2 variables a company added
        if ev["name"] in cat:
            continue
        st, frm = status.get(ev["name"], ("missing", None))
        rows.append({"name": ev["name"], "display_name": ev.get("display_name") or display_name(ev["name"]),
                     "description": ev.get("help"), "file_group": None, "needed_for": "questions", "status": st,
                     "mapped_from": frm if st == "mapped" else None, "proposed_from": frm if st != "mapped" else None})
    return Readiness(plan_ready=not blocking, blocking=blocking, variables=rows, code=code)


def coverage(datasets: list[DatasetState]) -> list[dict[str, Any]]:
    """Per file group: present or missing, date range, rows, and which factors are on/off (Gap 8)."""
    out = []
    for g, r in group_roles().items():
        if g == "custom":
            continue
        ds = [d for d in datasets if d.file_type in r["file_types"] and d.status == "ready"]
        present = bool(ds)
        froms = [d.data_from for d in ds if d.data_from is not None]
        tos = [d.data_to for d in ds if d.data_to is not None]
        out.append({
            "file_group": g, "label": r["label"], "plan_role": r["plan_role"], "present": present,
            "files": [d.file_name for d in ds], "rows": sum(int(d.rows or 0) for d in ds),
            "date_from": str(min(froms)) if froms else None, "date_to": str(max(tos)) if tos else None,
            "factors_on": list(r["factors"]) if present else [],
            "factors_off": [] if present else list(r["factors"]),
            "missing_means": None if present else r["missing_means"],
            "questions_blocked": None if present else r["questions"],
        })
    return out


def missing_groups(datasets: list[DatasetState]) -> list[str]:
    return [c["file_group"] for c in coverage(datasets) if not c["present"]]
