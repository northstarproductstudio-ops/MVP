"""Synthetic, anonymised source files shaped like the real exports (no client data in the repo).

Zoho SOs (with an org-2 column shift and a shifted address block), an Instamart PO CSV,
a Zepto PO CSV, a Blinkit DOH workbook (header on row 2, totals row, '-' values) and a
Dcluttr category-insight workbook (3-row header)."""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

import openpyxl

SKUS = [
    ("CHCCSA35", "The Select Aisle Dark Compound Chocolate Chips - 150g", 179, "Compound Chocolate & Cocoa"),
    ("BSLASA04", "The Select Aisle Baking Soda - 100g", 45, "Functional Ingredients"),
    ("CDTFSA01", "TSA Tutti Frutti - 100g", 60, "Cake Décor"),
    ("BICPSA01", "The Select Aisle Eggless Vanilla cake premix - 365g", 249, "Cake Mixes"),
]
CUSTOMERS = [
    ("Moonstone - Bengaluru B5 Feeder Warehouse", "Bengaluru", "Karnataka"),
    ("Superwell - Noida N1 Feeder Warehouse", "Greater Noida", "Uttar Pradesh"),
    ("Swiggy Instamart Chennai", "Chennai", "Tamil Nadu"),
    ("Zepto Limited-MUM-DRY", "Thane", "Maharashtra"),
    ("Symbiate Ventures Pvt Ltd Bangalore", "Bangalore", "Karnataka"),
]
ZOHO_HEADER = ["SalesOrder ID", "Order Date", "SalesOrder Number", "Status", "Customer Name", "Branch ID",
               "Reference#", "Item Name", "SKU", "QuantityOrdered", "QuantityInvoiced", "QuantityCancelled",
               "Usage unit", "Warehouse Name", "Item Price", "Shipping Street2", "Shipping City", "Shipping State",
               "Shipping Country", "Item.CF.MRP", ""]


def zoho_rows(seed: int = 7) -> list[list]:
    rnd = random.Random(seed)
    rows = []
    so = 1000
    for month, scale in ((7, 1.0), (8, 1.15), (9, 1.3)):
        for day in range(1, 29, 3):
            for ci, (cust, city, state) in enumerate(CUSTOMERS):
                so += 1
                d = date(2026, month, day)
                for sku, name, mrp, _ in SKUS:
                    q = int(rnd.randint(20, 60) * scale * (1 + ci * 0.2))
                    cancelled = rnd.choice([0, 0, 0, 4])
                    status = rnd.choice(["invoiced"] * 8 + ["confirmed", "void"])
                    org2 = ci == 3  # Zepto rows come from the org-2 branch
                    branch = "3854818000000815001" if org2 else "610303000103147657"
                    base = [f"{'3854' if org2 else '6103'}{so}", d, f"SO-{so}", status, cust, branch, f"REF{so}",
                            name, sku, q + cancelled, q, cancelled, "pcs"]
                    tail = ["Main WH", round(mrp * 0.6, 2), "", city, state, "India", mrp]
                    if org2:
                        # extra column inserted at Warehouse Name: everything shifts right by one,
                        # and the address block shifts once more (state in Shipping City)
                        tail = [branch, "Main WH", round(mrp * 0.6, 2), city, state, "India", "560001", mrp]
                    rows.append(base + tail)
    return rows


def write_zoho(path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(ZOHO_HEADER)
    for r in zoho_rows():
        ws.append(r)
    wb.save(path)
    return path


def write_instamart_po(path: Path) -> Path:
    hdr = ["PoNumber", "FacilityId", "FacilityName", "City", "PoCreatedAt", "Status", "SkuCode", "SkuDescription",
           "OrderedQty", "ReceivedQty", "BalancedQty", "Mrp", "UnitBasedCost", "ExpectedDeliveryDate", "PoExpiryDate"]
    items = [("449159", "The Select Aisle Dark Compound Chocolate Chips 150.0 g"),
             ("572811", "The Select Aisle Baking soda 100.0 g")]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(hdr)
        for i in range(12):
            d = date(2026, 8, 5) + timedelta(days=4 * i)
            for code, name in items:
                status = ["COMPLETED", "COMPLETED", "EXPIRED", "CONFIRMED"][i % 4]
                ordered = 120
                received = 90 if status == "COMPLETED" else 0
                w.writerow([f"PO{i}", "FC1", "CHN FC1", "CHENNAI", f"{d} 04:00:00", status, code, name, ordered,
                            received, ordered - received if status == "CONFIRMED" else 0, 179, 100,
                            str(d + timedelta(days=5)), str(d + timedelta(days=9))])
    return path


def write_zepto_po(path: Path) -> Path:
    hdr = ["PO No.", "PO Date", "Status", "Del Location", "SKU", "SKU Desc", "EAN", "Qty", "ASN Quantity",
           "GRN Quantity", "PO Expiry Date", "MRP/RSP", "Landing Cost"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(hdr)
        for i in range(6):
            d = date(2026, 8, 3) + timedelta(days=7 * i)
            w.writerow([f"P{i}", d.strftime("%d %b %Y 05:52 am"), "COMPLETED", "MUM-DRY-MH1-BHIWANDI",
                        "2CE0B6E4-92FA-4044-BEC7-10B62C50593A", "The Select Aisle Tutti Frutti - 1 pack (100 g)",
                        "8906144684101", 100, 0, 95, (d + timedelta(days=20)).strftime("%d %b %Y 11:59 pm"), 60, 40])
    return path


def write_blinkit_doh(path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Blinkit SOH - 18 Sep"
    ws.append([None] * 9 + [1000, 50.0, 20.0])
    ws.append(["created_at", "backend_facility_name", "City", "backend_facility_id", "item_id", "Item ID x City",
               "item_name", "backend_inv_qty", "frontend_inv_qty", "Total SOH", "RR", "DOH", None, "WH", "City"])
    ws.append([date(2026, 9, 18), "Bengaluru B5 - Feeder Warehouse", "Bengaluru", 1, 10155342, "10155342Bengaluru",
               "The Select Aisle Dark Choco Chips 150 g", 300, 120, 420, 30.0, 14.0, None, "x", "y"])
    ws.append([date(2026, 9, 18), "Noida N1 - Feeder Warehouse", "Noida", 2, 10155342, "10155342Noida",
               "The Select Aisle Dark Choco Chips 150 g", 0, 0, 0, "-", "-", None, None, None])
    wb.save(path)
    return path


def write_dcluttr(path: Path, platform: str = "blinkit") -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Categories Insight"
    ws.append(["Report generated at 03 Oct 2026, 02:30 PM IST"] + [""] * 18)
    ws.append(["Details", "", "", "", "", "", "Sales", "", "", "Market", "", "Pricing", "", "", "", "Availability",
               "Visibility", "", ""])
    ws.append(["Date", "Variant ID", "Item ID", "Product Name", "Brand", "Category", "Sales (MRP)", "Sales (SP)",
               "Raw Units", "Est Category Share MRP", "Est Category Share SP", "MRP", "SP", "WT PPU (x100)",
               "WT Discount", "Wt. OSA %", "Overall SOV", "Organic SOV", "Ad SOV"])
    for day in range(1, 31):
        d = f"2026-09-{day:02d}"
        ws.append([d, 547779.0, 10155342.0, "The Select Aisle Dark Choco Chips (Dark Chocolate)", "The Select Aisle",
                   "Choco Chips", 4000.0, 3600.0, "150 g", 20.0, 21.0, 179.0, 160.0, 100, 10.0, 80.0, 1, 1, 0])
        ws.append([d, 547780.0, None, "Rival Choco Chips", "Rival", "Choco Chips", 12000.0, 11000.0, "150 g",
                   60.0, 59.0, 199.0, 180.0, 100, 12.0, 90.0, 1, 1, 0])
    wb.save(path)
    return path


def write_all(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    return [
        write_zoho(folder / "SOs - Merged - JAS-2026.xlsx"),
        write_instamart_po(folder / "Swiggy PO Export - L60D.csv"),
        write_zepto_po(folder / "Zepto PO Export - Aug-2026.csv"),
        write_blinkit_doh(folder / "Blinkit DOH - 18 Sep.xlsx"),
        write_dcluttr(folder / "Blinkit_Categories_Insight_Sep-2026.xlsx"),
    ]
