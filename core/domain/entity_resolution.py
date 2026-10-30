"""Entity resolution: SKU crosswalk, customer → platform, raw city → planning city."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
from rapidfuzz import fuzz

from core.plan_config import SEED_DIR

# --------------------------------------------------------------------------- customers


@dataclass(frozen=True)
class CustomerRule:
    order: int
    match: str  # prefix | contains
    pattern: str
    platform: str


def seed_customer_rules() -> list[CustomerRule]:
    with open(SEED_DIR / "customer_rules.csv", encoding="utf-8") as fh:
        rules = [CustomerRule(int(r["order"]), r["match"], r["pattern"].lower(), r["platform"])
                 for r in csv.DictReader(fh)]
    return sorted(rules, key=lambda r: r.order)


def customer_platform(name: str | None, rules: list[CustomerRule]) -> tuple[str, str]:
    """Return (platform, rule text). Ordered rules; first hit wins; else 'other'."""
    n = (name or "").strip().lower()
    for r in rules:
        if (r.match == "prefix" and n.startswith(r.pattern)) or (r.match == "contains" and r.pattern in n):
            return r.platform, f"{r.match}:{r.pattern}"
    return "other", "default"


def build_customer_map(names: pd.Series, rules: list[CustomerRule],
                       existing: pd.DataFrame | None = None) -> pd.DataFrame:
    known = {} if existing is None or existing.empty else dict(zip(existing.customer_name, existing.platform, strict=False))
    rows = []
    for n in sorted({str(x) for x in names.dropna().unique()}):
        if n in known:
            rows.append({"customer_name": n, "platform": known[n], "rule": "saved"})
        else:
            p, rule = customer_platform(n, rules)
            rows.append({"customer_name": n, "platform": p, "rule": rule})
    return pd.DataFrame(rows, columns=["customer_name", "platform", "rule"])


# --------------------------------------------------------------------------- cities


def seed_city_aliases() -> dict[str, str]:
    with open(SEED_DIR / "city_aliases.csv", encoding="utf-8") as fh:
        return {r["raw_city"].strip().lower(): r["city"] for r in csv.DictReader(fh)}


def _norm_city(raw: Any) -> str:
    s = str(raw or "").lower()
    s = re.sub(r"[^a-z ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def resolve_city(raw: Any, aliases: dict[str, str]) -> str:
    """Exact alias, else the longest alias appearing as whole words, else Other."""
    n = _norm_city(raw)
    if not n:
        return "Other"
    if n in aliases:
        return aliases[n]
    padded = f" {n} "
    for alias in sorted(aliases, key=len, reverse=True):
        if f" {alias} " in padded:
            return aliases[alias]
    return "Other"


def city_series(raw: pd.Series, aliases: dict[str, str]) -> pd.Series:
    uniq = {v: resolve_city(v, aliases) for v in raw.dropna().unique()}
    return raw.map(uniq).fillna("Other")


def unmatched_cities(sellin: pd.DataFrame, min_share: float) -> list[dict[str, Any]]:
    """Raw ship cities resolving to Other that carry more than ``min_share`` of volume."""
    if sellin.empty:
        return []
    q = sellin["qty_ordered"].fillna(sellin["qty_invoiced"]).fillna(0).astype(float)
    total = q.sum() or 1.0
    other = sellin[sellin["city"] == "Other"]
    vol = q[other.index].groupby(other["ship_city_raw"]).sum().sort_values(ascending=False)
    return [{"raw_city": k, "share": round(v / total, 4)} for k, v in vol.items() if v / total > min_share]


# --------------------------------------------------------------------------- SKU crosswalk

_PACK_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|kgs|g|gm|gms|gram|grams|ml|l|ltr|litre)\b", re.I)


def parse_pack(text: Any) -> float | None:
    """Pack size in grams or millilitres ('20 g', '200g', '1 pack (200 g)', '500 ml', '1kg')."""
    matches = list(_PACK_RE.finditer(str(text or "")))
    m = matches[-1] if matches else None  # last match wins ("1 pack (200 g)")
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2).lower()
    if unit in ("kg", "kgs", "l", "ltr", "litre"):
        val *= 1000
    return round(val, 1)


SYNONYMS = {
    "choco": "chocolate", "chocolates": "chocolate", "color": "colour", "colors": "colour",
    "colours": "colour", "sugarball": "sugar ball", "sugarballs": "sugar ball", "balls": "ball",
    "cupcake": "cup cake", "premix": "mix", "gel": "gel", "chips": "chip", "sprinkle": "sprinkles",
    "flavor": "flavour", "crumb": "crumbs", "marshmallows": "marshmallow", "yeast": "yeast",
}
STOPWORDS = {"pack", "of", "x", "pouch", "jar", "pc", "pcs", "the", "and", "for", "with", "a", "in",
             "essential", "baking essential", "ingredient", "combo", "gm", "g", "ml"}


def normalise_name(text: Any, strip_tokens: list[str]) -> str:
    s = str(text or "").split("|")[0].lower()
    for tok in sorted(strip_tokens, key=len, reverse=True):
        s = re.sub(rf"\b{re.escape(tok.lower())}('s)?\b", " ", s)
    s = _PACK_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = [SYNONYMS.get(w, w) for w in s.split()]
    words = [w for w in " ".join(words).split() if w not in STOPWORDS and not w.isdigit()]
    return " ".join(words)


@dataclass
class SkuRef:
    sku_code: str
    name: str
    norm: str
    pack: float | None
    ean: str | None


def sku_refs(sellin: pd.DataFrame, sku_master: pd.DataFrame, strip_tokens: list[str]) -> list[SkuRef]:
    names: dict[str, str] = {}
    eans: dict[str, str] = {}
    if not sku_master.empty:
        for r in sku_master.itertuples():
            if r.item_name:
                names[r.sku_code] = r.item_name
            if getattr(r, "ean", None):
                eans[r.sku_code] = str(r.ean)
    pairs = {(s, n) for s, n in names.items()}
    if not sellin.empty:
        valid = sellin.dropna(subset=["item_name", "sku_code"])
        valid = valid[valid["sku_code"].str.fullmatch(r"[A-Za-z0-9_\-]+")]
        pairs |= set(zip(valid["sku_code"], valid["item_name"], strict=False))
    return [SkuRef(s, n, normalise_name(n, strip_tokens), parse_pack(n), eans.get(s)) for s, n in sorted(pairs)]


def match_item(platform_name: str, ean: str | None, refs: list[SkuRef], strip_tokens: list[str],
               tie_margin: float = 0.01) -> tuple[str | None, float, str, list[dict[str, Any]]]:
    """Best SKU for a platform item. Method ``tie`` when the runner-up scores within
    ``tie_margin`` of the top (e.g. two SKUs with the same name and pack): never auto-accepted."""
    if ean:
        for r in refs:
            if r.ean and r.ean == ean:
                return r.sku_code, 1.0, "ean", []
    norm = normalise_name(platform_name, strip_tokens)
    pack = parse_pack(platform_name)
    best: dict[str, float] = {}
    pack_known: dict[str, bool] = {}
    for r in refs:
        s = fuzz.token_set_ratio(norm, r.norm) / 100.0
        if pack is not None and r.pack is not None:
            if abs(pack - r.pack) > 0.5:
                continue  # pack sizes must be equal
        else:
            s *= 0.95  # pack size unknown on one side: can only reach review
        if s > best.get(r.sku_code, -1.0):
            best[r.sku_code] = s
            pack_known[r.sku_code] = pack is not None and r.pack is not None
    scored = sorted(((v, k) for k, v in best.items()), reverse=True)
    cands = [{"sku_code": c, "score": round(s, 4)} for s, c in scored[:3]]
    if not scored:
        return None, 0.0, "unmatched", []
    top = scored[0][1]
    if len(scored) > 1 and scored[0][0] - scored[1][0] < tie_margin:
        return top, round(scored[0][0], 4), "tie", cands
    return top, round(scored[0][0], 4), "auto" if pack_known.get(top) else "auto_nopack", cands


def build_crosswalk(items: pd.DataFrame, refs: list[SkuRef], existing: pd.DataFrame | None,
                    strip_tokens: list[str], auto_accept: float, review_min: float,
                    tie_margin: float = 0.01) -> pd.DataFrame:
    """Draft crosswalk rows for platform items. Confirmed rows are never overwritten."""
    keep: dict[tuple[str, str], dict[str, Any]] = {}
    if existing is not None and not existing.empty:
        for r in existing.to_dict("records"):
            keep[(r["platform"], str(r["platform_item_id"]))] = r
    rows = []
    seen = set()
    for it in items.drop_duplicates(["platform", "platform_item_id"]).to_dict("records"):
        k = (it["platform"], str(it["platform_item_id"]))
        if k in seen or not it["platform_item_id"]:
            continue
        seen.add(k)
        prev = keep.get(k)
        if prev and (prev.get("confirmed_at") or prev.get("match_method") in ("manual", "ean")):
            rows.append(prev)
            continue
        sku, score, method, cands = match_item(it.get("platform_name") or "", it.get("ean"), refs, strip_tokens,
                                               tie_margin)
        if method in ("auto", "auto_nopack", "tie"):
            if score >= auto_accept and method == "auto":
                method = "auto"
            elif score >= review_min:
                method, sku = "review", sku
            else:
                method, sku = "unmatched", None
        rows.append({"platform": it["platform"], "platform_item_id": str(it["platform_item_id"]),
                     "platform_name": it.get("platform_name"),
                     "sku_code": sku if method in ("auto", "ean") else None,
                     "match_method": method, "score": score,
                     "candidates": json.dumps(cands), "confirmed_by": None, "confirmed_at": None,
                     "proposed_sku": sku})
    for k, prev in keep.items():
        if k not in seen:
            rows.append(prev)
    return pd.DataFrame(rows)


def platform_items(po: pd.DataFrame, stock: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    frames = []
    if not po.empty:
        frames.append(po[["platform", "platform_item_id", "platform_name", "ean"]])
    if not stock.empty:
        frames.append(stock[["platform", "platform_item_id", "platform_name"]].assign(ean=None))
    if not market.empty:
        own = market[market["is_own_brand"]]
        named = own.assign(platform_name=own["product_name"].fillna("") + " " + own["pack_size"].fillna(""))
        frames.append(named[["platform", "platform_item_id", "platform_name"]].assign(ean=None))
    if not frames:
        return pd.DataFrame(columns=["platform", "platform_item_id", "platform_name", "ean"])
    out = pd.concat(frames, ignore_index=True).dropna(subset=["platform_item_id"])
    # prefer rows with a name / EAN
    out = out.sort_values(["ean", "platform_name"], na_position="last")
    return out.drop_duplicates(["platform", "platform_item_id"])


def apply_crosswalk(df: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    if df.empty or crosswalk.empty:
        if "sku_code" not in df:
            df = df.assign(sku_code=None)
        return df
    accepted = crosswalk[crosswalk["match_method"].isin(["ean", "auto", "manual"]) & crosswalk["sku_code"].notna()]
    m = dict(zip(zip(accepted["platform"], accepted["platform_item_id"].astype(str), strict=False), accepted["sku_code"], strict=False))
    keys = list(zip(df["platform"], df["platform_item_id"].astype("string").fillna(""), strict=False))
    df = df.copy()
    df["sku_code"] = [m.get(k) for k in keys]
    return df
