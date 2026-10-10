from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.domain import file_types
from core.domain.file_reader import preview
from core.domain.importing import import_file, parse_file


def _find(d: Path, part: str) -> Path:
    return next(p for p in d.iterdir() if part.lower() in p.name.lower())


def test_detection_tags_every_sample(sample_dir, cfg):
    expected = {"SOs": "erp_sales_orders", "Swiggy": "platform_po_instamart", "Zepto PO": "platform_po_zepto",
                "DOH": "platform_stock_blinkit", "Insight": "market_category_insight"}
    for part, key in expected.items():
        f = _find(sample_dir, part)
        p = parse_file(f, f.name, cfg)
        assert p.proposal.file_type == key, f.name
        assert not p.proposal.ask_user


def test_content_beats_filename(sample_dir, cfg):
    f = _find(sample_dir, "Swiggy")
    det = file_types.detect(preview(f), "zepto-export.csv")
    assert det.file_type == "platform_po_instamart"
    assert det.decided is False  # name and content conflict → ask


def test_unknown_file_is_custom(tmp_path, cfg):
    f = tmp_path / "notes.csv"
    f.write_text("a,b,c\n1,2,3\n")
    p = parse_file(f, f.name, cfg)
    assert p.proposal.file_type == "custom"


def test_zoho_org2_shift_and_address_fix(sample_dir, cfg):
    f = _find(sample_dir, "SOs")
    parsed, res = import_file(f, f.name, cfg)
    df = res.frame
    assert any("org-2" in w for w in res.warnings)
    assert any("Shipping Street2" in w for w in res.warnings)
    zepto = df[df["customer_name"].str.startswith("Zepto")]
    assert (zepto["item_price"] > 0).all()           # price read from the right column after the shift
    assert set(zepto["ship_city_raw"]) == {"Thane"}  # city, not the state
    assert not df["status"].isin(["void", "draft"]).any()
    assert df["line_key"].is_unique


def test_hard_fields_recognised(sample_dir, cfg):
    f = _find(sample_dir, "SOs")
    p = parse_file(f, f.name, cfg).proposal
    mapped = {x.variable for x in p.fields if x.state == "recognised"}
    assert {"sku_code", "order_date", "so_status", "customer_name", "ship_city", "qty_invoiced"} <= mapped
    assert not p.warnings


def test_blinkit_dash_is_null_not_zero(sample_dir, cfg):
    f = _find(sample_dir, "DOH")
    _, res = import_file(f, f.name, cfg)
    df = res.frame
    assert len(df) == 2  # totals row dropped (header on row 2)
    noida = df[df["city_raw"] == "Noida"].iloc[0]
    assert pd.isna(noida["run_rate"]) and pd.isna(noida["doh"])
    blr = df[df["city_raw"] == "Bengaluru"].iloc[0]
    assert blr["soh_units"] == 420  # backend + frontend
    assert "col_13" not in df.columns


def test_zepto_po_dates_uuid_and_city(sample_dir, cfg):
    f = _find(sample_dir, "Zepto PO")
    _, res = import_file(f, f.name, cfg)
    df = res.frame
    assert df["po_date"].notna().all()
    assert df["platform_item_id"].str.islower().all()
    assert set(df["city_raw"]) == {"MUM"}


def test_instamart_open_qty_only_on_confirmed(sample_dir, cfg):
    f = _find(sample_dir, "Swiggy")
    _, res = import_file(f, f.name, cfg)
    df = res.frame
    assert (df.loc[df["status"] != "open", "qty_open"] == 0).all()
    assert (df.loc[df["status"] == "open", "qty_open"] > 0).all()
    assert set(df["city_raw"]) == {"Chennai"}


def test_dcluttr_platform_and_own_brand(sample_dir, cfg):
    f = _find(sample_dir, "Insight")
    _, res = import_file(f, f.name, cfg)
    df = res.frame
    assert set(df["platform"]) == {"blinkit"}
    own = df[df["is_own_brand"]]
    assert set(own["platform_item_id"]) == {"10155342"}
    assert set(df.loc[~df["is_own_brand"], "platform_item_id"]) == {"547780"}  # falls back to variant id
