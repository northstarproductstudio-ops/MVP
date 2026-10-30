from __future__ import annotations

import json

import pandas as pd

from core.domain import entity_resolution as er


def test_parse_pack():
    assert er.parse_pack("20 g") == 20
    assert er.parse_pack("TSA Tutti Frutti - 100g") == 100
    assert er.parse_pack("Kunafa Shreds - 1 pack (200 g)") == 200
    assert er.parse_pack("Vanilla 500 ml") == 500
    assert er.parse_pack("Baking Soda - 1kg") == 1000
    assert er.parse_pack("Yeast 25.0 g") == 25
    assert er.parse_pack("no pack") is None


def test_customer_rules():
    rules = er.seed_customer_rules()
    assert er.customer_platform("Moonstone - Bengaluru B5", rules)[0] == "blinkit"
    assert er.customer_platform("Superwell - Dasna 3", rules)[0] == "blinkit"
    assert er.customer_platform("SCOOTSY LOGISTICS", rules)[0] == "instamart"
    assert er.customer_platform("Innovative Retail Concepts", rules)[0] == "bigbasket"
    assert er.customer_platform("SYMBIATE VENTURES PVT LTD", rules)[0] == "internal"
    assert er.customer_platform("Acme Stores", rules)[0] == "other"


def test_city_resolution():
    a = er.seed_city_aliases()
    assert er.resolve_city("Bengaluru Urban", a) == "Bangalore"
    assert er.resolve_city("SECUNDRABAD", a) == "Hyderabad"
    assert er.resolve_city("Bhiwandi Tahashil", a) == "Mumbai"
    assert er.resolve_city("CHN", a) == "Chennai"
    assert er.resolve_city("Gurugram", a) == "Delhi"
    assert er.resolve_city("Kolkata", a) == "Other"
    assert er.resolve_city(None, a) == "Other"


def test_crosswalk_requires_equal_pack_and_respects_confirmed():
    strip = ["The Select Aisle", "TSA"]
    refs = [er.SkuRef("CHCCSA35", "Dark Compound Chocolate Chips - 150g",
                      er.normalise_name("Dark Compound Chocolate Chips - 150g", strip), 150, None),
            er.SkuRef("CHCCSA48", "TSA Dark Compound Chocolate Chips 50g",
                      er.normalise_name("TSA Dark Compound Chocolate Chips 50g", strip), 50, None)]
    items = pd.DataFrame([
        {"platform": "zepto", "platform_item_id": "u1", "platform_name": "The Select Aisle Dark Compound Chocolate Chips - 1 pack (50 g)", "ean": None},
        {"platform": "zepto", "platform_item_id": "u2", "platform_name": "Something else entirely 999 g", "ean": None},
        {"platform": "zepto", "platform_item_id": "u3", "platform_name": "Dark Chips 150 g", "ean": None},
    ])
    existing = pd.DataFrame([{"platform": "zepto", "platform_item_id": "u3", "platform_name": "Dark Chips 150 g",
                              "sku_code": "CHCCSA35", "match_method": "manual", "score": 1.0, "candidates": "[]",
                              "confirmed_by": "u", "confirmed_at": "2026-10-01"}])
    cw = er.build_crosswalk(items, refs, existing, strip, 0.9, 0.75).set_index("platform_item_id")
    assert cw.loc["u1", "sku_code"] == "CHCCSA48" and cw.loc["u1", "match_method"] == "auto"
    assert cw.loc["u2", "match_method"] == "unmatched"
    assert cw.loc["u3", "match_method"] == "manual"  # confirmed rows are never overwritten


def test_same_name_and_pack_goes_to_review_not_auto():
    strip = ["The Select Aisle", "TSA"]
    name = "The Select Aisle Dutch Processed Cocoa Powder 250 g"
    refs = [er.SkuRef(s, name, er.normalise_name(name, strip), 250, None) for s in ("CHCOSA01", "CHCOSA02")]
    items = pd.DataFrame([{"platform": "blinkit", "platform_item_id": "10523400", "platform_name": name, "ean": None}])
    row = er.build_crosswalk(items, refs, None, strip, 0.9, 0.75).iloc[0]
    assert row["match_method"] == "review" and row["sku_code"] is None
    assert {c["sku_code"] for c in json.loads(row["candidates"])} == {"CHCOSA01", "CHCOSA02"}
    _, _, method, _ = er.match_item(name, None, refs, strip)
    assert method == "tie"


def test_ean_match_wins():
    refs = [er.SkuRef("X1", "Foo 100g", "foo", 100, "890123")]
    sku, score, method, _ = er.match_item("Totally different 5 g", "890123", refs, [])
    assert (sku, score, method) == ("X1", 1.0, "ean")
