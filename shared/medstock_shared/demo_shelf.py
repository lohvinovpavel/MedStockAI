"""NDCs the inventory dashboard shows.

One list so seed_stock, seed_demo and COMP-1 cannot drift. Warehouse needs
`drug` + `stock_snapshot` + a consumption series for these or the picker
shows unnamed rows with empty charts. `storage_class` places each SKU on
the right shelf (insulin in a fridge, not the main room).

Wave 2: `quantity` / `lot` / `expiry_days` are the live inventory row.
`par_reorder` / `par_target` make B5 status a real claim — the four
critical story SKUs (ceftriaxone, norepinephrine, insulin, heparin) sit
at or below reorder. Facility profiles copy the old per-site depth so
switching site still changes coverage the same way.

Wave 3: `rxcui` is the clinical id B6 writes to `formulary_item` and B3
joins through. Values come from analogue RxCUIs / the B6 spec
example (norepinephrine 1049640). Demo NDCs often have no live RxNorm
pack list, so resolvers union this map with `ndcs_for_rxcui`.

Wave 4: partner-site stock for the three shortage SKUs and the four
suppliers (unit costs, lead times) so G1/F2 stay live.

Wave 5: `DEMO_ORDERS` plants the purchase-order history `/orders` shows
(PO-2026-0141 … 0148).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .demo_tenant import FACILITIES, location_for

# storage_min/max and humidity match warehouse CLASS_RANGES / seed_demo drugs.csv.
# `id` is a stable shelf-row key so FACILITY_SHELF_PROFILE.absent
# can name the same rows omitted per site.
DASHBOARD_SHELF: tuple[dict, ...] = (
    {
        "id": "inv-001",
        "rxcui": "562508",
        "ndc": "62135009120",
        "name": "Amoxicillin/Clavulanate 875mg",
        "lot": "AMX-24118-B",
        "quantity": 900,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
        "par_reorder": 200,
        "par_target": 600,
        "expiry_days": 21,
    },
    {
        "id": "inv-002",
        "rxcui": "203155",
        "ndc": "16714097720",
        "name": "Propofol 1% Emulsion",
        "lot": "PPF-24902-C",
        "quantity": 250,
        "storage_class": "refrigerated",
        "storage_min_c": 2.0,
        "storage_max_c": 8.0,
        "humidity_max_pct": 75.0,
        "par_reorder": 80,
        "par_target": 200,
        "expiry_days": 65,
    },
    {
        "id": "inv-003",
        "rxcui": "309090",
        "ndc": "82804006601",
        "name": "Ceftriaxone 1g",
        "lot": "CFX-25011-A",
        "quantity": 9,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
        "par_reorder": 20,
        "par_target": 80,
        "expiry_days": 8,
    },
    {
        "id": "inv-004",
        "rxcui": "745679",
        "ndc": "00487990130",
        "name": "Salbutamol 100mcg Inhaler",
        "lot": "SLB-24775-D",
        "quantity": 310,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
        "par_reorder": 40,
        "par_target": 200,
        "expiry_days": 340,
    },
    {
        "id": "inv-005",
        "rxcui": "1049640",
        "ndc": "00338011220",
        "name": "Norepinephrine 4mg/4mL",
        "lot": "NEP-25033-A",
        "quantity": 6,
        "storage_class": "refrigerated",
        "storage_min_c": 2.0,
        "storage_max_c": 8.0,
        "humidity_max_pct": 75.0,
        "par_reorder": 80,
        "par_target": 200,
        "expiry_days": 3,
    },
    {
        "id": "inv-006",
        "rxcui": "248656",
        "ndc": "00069406101",
        "name": "Azithromycin 250mg",
        "lot": "AZT-24610-B",
        "quantity": 520,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
        "par_reorder": 100,
        "par_target": 400,
        "expiry_days": 190,
    },
    {
        "id": "inv-007",
        "rxcui": "1157459",
        "ndc": "00024586900",
        "name": "Insulin Glargine 100U/mL",
        "lot": "IGL-25102-A",
        "quantity": 26,
        "storage_class": "refrigerated",
        "storage_min_c": 2.0,
        "storage_max_c": 8.0,
        "humidity_max_pct": 75.0,
        "par_reorder": 90,
        "par_target": 200,
        "expiry_days": 27,
    },
    {
        "id": "inv-008",
        "rxcui": "311704",
        "ndc": "63323041125",
        "name": "Midazolam 5mg/mL",
        "lot": "MDZ-24988-C",
        "quantity": 90,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
        "par_reorder": 50,
        "par_target": 200,
        "expiry_days": 55,
    },
    {
        "id": "inv-009",
        "rxcui": "198440",
        "ndc": "00143938610",
        "name": "Paracetamol 1g IV",
        "lot": "PCM-25064-B",
        "quantity": 410,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
        "par_reorder": 80,
        "par_target": 250,
        "expiry_days": 410,
    },
    {
        "id": "inv-010",
        "rxcui": "1361574",
        "ndc": "00338043304",
        "name": "Heparin Sodium 5000IU/mL",
        "lot": "HEP-24855-A",
        "quantity": 5,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
        "par_reorder": 100,
        "par_target": 240,
        "expiry_days": 14,
    },
    {
        "id": "inv-011",
        "rxcui": "204541",
        "ndc": "76168080030",
        "name": "Carmellose Sodium 0.5% Eye Drops",
        "lot": "CMC-24310-A",
        "quantity": 62,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
        "par_reorder": 20,
        "par_target": 80,
        "expiry_days": 180,
    },
)

# Per-site depth: clinics omit ICU SKUs; warehouse is bulk.
# `stock_factor` scales on-hand qty; forecasts read `consumption_daily`, not a burn factor.
FACILITY_SHELF_PROFILE: dict[str, dict] = {
    "central": {"stock_factor": 1.0, "absent": ()},
    "riverside": {"stock_factor": 0.35, "absent": ("inv-002", "inv-005")},
    "westend": {"stock_factor": 0.22, "absent": ("inv-002", "inv-005", "inv-008")},
    "warehouse-north": {"stock_factor": 7.0, "absent": ("inv-007",)},
}


def lot_for(item: dict, facility_code: str) -> str:
    """Lot suffix: last two chars of the facility code, uppercased."""
    lot = str(item["lot"])
    if facility_code == "central":
        return lot
    return f"{lot}-{facility_code[-2:].upper()}"


def shelf_stock_rows(hospital_id, fac_ids: dict[str, int]) -> list[dict]:
    """One snapshot+batch line per operated facility that stocks the SKU."""
    rows: list[dict] = []
    for fac in FACILITIES:
        if not fac["operated"]:
            continue
        profile = FACILITY_SHELF_PROFILE[fac["code"]]
        for item in DASHBOARD_SHELF:
            if item["id"] in profile["absent"]:
                continue
            loc = location_for(fac["code"], item["storage_class"])
            if loc is None:
                continue
            qty = max(0, round(int(item["quantity"]) * float(profile["stock_factor"])))
            rows.append(
                {
                    "hospital_id": hospital_id,
                    "ndc": item["ndc"],
                    "facility_id": fac_ids[fac["code"]],
                    "location_id": loc,
                    "quantity": qty,
                    "lot": lot_for(item, fac["code"]),
                    "expiry_days": int(item["expiry_days"]),
                    "par_reorder": int(item["par_reorder"]),
                    "par_target": int(item["par_target"]),
                    "shelf_id": item["id"],
                }
            )
    return rows


# Demo shortage alerts (Norepinephrine, Ceftriaxone, Heparin).
# Those SKUs sit at/below par from the B5 seed, so B3 `uncovered` is a real claim.
# Identified by shelf `id` so the COMP-1 NDC regex still sees exactly 11 NDCs.
DEMO_SHORTAGE_SPECS: tuple[dict, ...] = (
    {
        "id": "inv-005",
        "source_id": "FDA-2026-0142",
        "status": "Currently in Shortage",
        "note": "Manufacturing delay, national backorder through Q4.",
        "agency": "FDA",
    },
    {
        "id": "inv-003",
        "source_id": "EMA-2026-0091",
        "status": "Currently in Shortage",
        "note": "Reduced allocation, 2 of 3 suppliers affected.",
        "agency": "EMA",
    },
    {
        "id": "inv-010",
        "source_id": "FDA-2026-0310",
        "status": "Currently in Shortage",
        "note": "Raw material shortage reported by manufacturer.",
        "agency": "FDA",
    },
)

# Partner-facility figures for the shortage matrix. Missing entries mean
# no visibility (omit the snapshot), not zero stock. days_of_supply is
# planted as trailing consumption so G1/E2 agree.
PARTNER_SHORTAGE_STOCK: dict[str, dict[str, dict]] = {
    "inv-005": {
        "st-luke": {"units": 0, "days_of_supply": 0},
        "mercy": {"units": 0, "days_of_supply": 0},
    },
    "inv-003": {
        "st-luke": {"units": 210, "days_of_supply": 70},
    },
    "inv-010": {
        "st-luke": {"units": 12, "days_of_supply": 8},
        "mercy": {"units": 64, "days_of_supply": 61},
    },
}

# Demo suppliers — catalog keyed by shelf `id`.
# Pack size 1 / min 1 so F3 can consume these unit costs as-is.
DEMO_SUPPLIERS: tuple[dict, ...] = (
    {
        "name": "PharmaSource Global Ltd.",
        "lead_time_days": 5,
        "reliability_pct": "98.20",
        "shipping_flat": "120.00",
        "catalog": {
            "inv-001": "12.4000",
            "inv-002": "3.8500",
            "inv-003": "8.9000",
            "inv-004": "14.2000",
            "inv-005": "22.5000",
            "inv-006": "6.1000",
            "inv-007": "41.0000",
            "inv-008": "5.4000",
            "inv-009": "3.2000",
            "inv-010": "9.7500",
        },
    },
    {
        "name": "Meditech Distribution Co.",
        "lead_time_days": 7,
        "reliability_pct": "95.60",
        "shipping_flat": "80.00",
        "catalog": {
            "inv-001": "11.8000",
            "inv-002": "4.0500",
            "inv-003": "8.2000",
            "inv-004": "15.1000",
            "inv-005": "21.4000",
            "inv-006": "6.5500",
            "inv-007": "43.5000",
            "inv-008": "5.1000",
            "inv-009": "3.4500",
            "inv-010": "9.1000",
        },
    },
    {
        "name": "EuroPharm Wholesale AG",
        "lead_time_days": 3,
        "reliability_pct": "99.10",
        "shipping_flat": "210.00",
        "catalog": {
            "inv-001": "13.6000",
            "inv-002": "4.2000",
            "inv-003": "9.6000",
            "inv-004": "13.4000",
            "inv-005": "24.8000",
            "inv-006": "6.9000",
            "inv-007": "39.2000",
            "inv-008": "6.0000",
            "inv-009": "3.6000",
            "inv-010": "10.4000",
        },
    },
    {
        "name": "Nordic Medical Supply",
        "lead_time_days": 11,
        "reliability_pct": "92.40",
        "shipping_flat": "45.00",
        "catalog": {
            "inv-001": "10.9000",
            "inv-002": "3.6000",
            "inv-003": "7.8000",
            "inv-004": "16.0000",
            "inv-005": "20.1000",
            "inv-006": "5.7000",
            "inv-007": "45.8000",
            "inv-008": "4.8500",
            "inv-009": "2.9500",
            "inv-010": "8.6000",
        },
    },
)

# Seeded purchase orders so /orders is populated after a regen.
# Dates are offsets from today. unit_cost is taken from DEMO_SUPPLIERS at seed time.
DEMO_ORDERS: tuple[dict, ...] = (
    {
        "ref": "PO-2026-0148",
        "facility": "central",
        "supplier": "EuroPharm Wholesale AG",
        "shelf_id": "inv-005",
        "quantity": 120,
        "status": "in_transit",
        "source": "ai_suggestion",
        "created_days_ago": 2,
        "expected_days": 1,
        "note": "Expedited against FDA national backorder.",
    },
    {
        "ref": "PO-2026-0147",
        "facility": "central",
        "supplier": "PharmaSource Global Ltd.",
        "shelf_id": "inv-003",
        "quantity": 300,
        "status": "in_transit",
        "source": "manual",
        "created_days_ago": 3,
        "expected_days": 2,
        "note": None,
    },
    {
        "ref": "PO-2026-0146",
        "facility": "riverside",
        "supplier": "Meditech Distribution Co.",
        "shelf_id": "inv-001",
        "quantity": 220,
        "status": "placed",
        "source": "manual",
        "created_days_ago": 4,
        "expected_days": 3,
        "note": None,
    },
    {
        "ref": "PO-2026-0145",
        "facility": "central",
        "supplier": "PharmaSource Global Ltd.",
        "shelf_id": "inv-010",
        "quantity": 180,
        "status": "delivered",
        "source": "ai_suggestion",
        "created_days_ago": 12,
        "expected_days": -6,
        "note": "Generated from 91.5% confidence forecast.",
    },
    {
        "ref": "PO-2026-0144",
        "facility": "warehouse-north",
        "supplier": "Nordic Medical Supply",
        "shelf_id": "inv-006",
        "quantity": 1400,
        "status": "delivered",
        "source": "manual",
        "created_days_ago": 16,
        "expected_days": -4,
        "note": None,
    },
    {
        "ref": "PO-2026-0143",
        "facility": "westend",
        "supplier": "Meditech Distribution Co.",
        "shelf_id": "inv-004",
        "quantity": 90,
        "status": "delivered",
        "source": "manual",
        "created_days_ago": 21,
        "expected_days": -13,
        "note": None,
    },
    {
        "ref": "PO-2026-0142",
        "facility": "central",
        "supplier": "EuroPharm Wholesale AG",
        "shelf_id": "inv-007",
        "quantity": 60,
        "status": "cancelled",
        "source": "manual",
        "created_days_ago": 24,
        "expected_days": -20,
        "note": "Cancelled — EMA certificate lapsed before dispatch.",
    },
    {
        "ref": "PO-2026-0141",
        "facility": "riverside",
        "supplier": "PharmaSource Global Ltd.",
        "shelf_id": "inv-009",
        "quantity": 500,
        "status": "delivered",
        "source": "ai_suggestion",
        "created_days_ago": 29,
        "expected_days": -23,
        "note": None,
    },
)


def formulary_rxcuis() -> list[str]:
    """Hospital formulary for a fresh demo — dashboard SKUs, unique, stable order."""
    out: list[str] = []
    seen: set[str] = set()
    for item in DASHBOARD_SHELF:
        rxcui = str(item.get("rxcui") or "")
        if rxcui and rxcui not in seen:
            seen.add(rxcui)
            out.append(rxcui)
    return out


def demo_shortage_rows() -> list[dict]:
    """`shortage_event` payloads aligned with DEMO_SHORTAGE_SPECS."""
    by_id = {item["id"]: item for item in DASHBOARD_SHELF}
    rows: list[dict] = []
    for spec in DEMO_SHORTAGE_SPECS:
        item = by_id[spec["id"]]
        rows.append(
            {
                "source_id": spec["source_id"],
                "ndc": item["ndc"],
                "status": spec["status"],
                "raw": {
                    "note": spec["note"],
                    "name": item["name"],
                    "source": "demo-wave3",
                    "agency": spec["agency"],
                    "rxcui": item["rxcui"],
                },
            }
        )
    return rows


def partner_shortage_stock_rows(hospital_id, fac_ids: dict[str, int]) -> list[dict]:
    """Snapshots for partner sites (st-luke, mercy) on the three shortage SKUs."""
    by_id = {item["id"]: item for item in DASHBOARD_SHELF}
    rows: list[dict] = []
    for shelf_id, sites in PARTNER_SHORTAGE_STOCK.items():
        item = by_id[shelf_id]
        for fac_code, est in sites.items():
            loc = location_for(fac_code, item["storage_class"])
            if loc is None or fac_code not in fac_ids:
                continue
            rows.append(
                {
                    "hospital_id": hospital_id,
                    "ndc": item["ndc"],
                    "facility_id": fac_ids[fac_code],
                    "facility_code": fac_code,
                    "location_id": loc,
                    "quantity": int(est["units"]),
                    "days_of_supply": int(est["days_of_supply"]),
                    "lot": lot_for(item, fac_code),
                    "expiry_days": int(item["expiry_days"]),
                    "rxcui": item["rxcui"],
                    "shelf_id": shelf_id,
                }
            )
    return rows


def partner_shortage_consumption_rows(
    hospital_id, fac_ids: dict[str, int], *, days: int = 28
) -> list[dict]:
    """Trailing consumption so G1 days-of-supply matches PARTNER_SHORTAGE_STOCK.

    `consumption_daily.qty_consumed` is int, so the 28-day series is an
    integer mix whose mean reconstructs units/days (largest remainder).
    """
    today = datetime.now(tz=UTC).date()
    rows: list[dict] = []
    for stock in partner_shortage_stock_rows(hospital_id, fac_ids):
        qty = int(stock["quantity"])
        dos = int(stock["days_of_supply"])
        if qty <= 0 or dos <= 0:
            continue
        daily = qty / dos
        base = int(daily)
        extras = min(days, max(0, round((daily - base) * days)))
        series = [base + 1] * extras + [base] * (days - extras)
        for offset, consumed in enumerate(series):
            if consumed <= 0:
                continue
            rows.append(
                {
                    "hospital_id": hospital_id,
                    "facility_id": stock["facility_id"],
                    "ndc": stock["ndc"],
                    "rxcui": stock["rxcui"],
                    "date": today - timedelta(days=offset),
                    "qty_consumed": consumed,
                    "stockout": False,
                }
            )
    return rows


def demo_supplier_rows(hospital_id) -> tuple[list[dict], list[dict]]:
    """Supplier + catalog rows keyed by DEMO_SUPPLIERS names / dashboard NDCs."""
    by_id = {item["id"]: item for item in DASHBOARD_SHELF}
    suppliers: list[dict] = []
    catalog: list[dict] = []
    for spec in DEMO_SUPPLIERS:
        suppliers.append(
            {
                "hospital_id": hospital_id,
                "name": spec["name"],
                "lead_time_days": int(spec["lead_time_days"]),
                "reliability_pct": spec["reliability_pct"],
                "shipping_flat": spec["shipping_flat"],
                "currency": "USD",
                "active": True,
            }
        )
        for shelf_id, unit_cost in spec["catalog"].items():
            item = by_id.get(shelf_id)
            if item is None:
                continue
            catalog.append(
                {
                    "supplier_name": spec["name"],
                    "ndc": item["ndc"],
                    "unit_cost": unit_cost,
                    "pack_size": 1,
                    "min_order_qty": 1,
                }
            )
    return suppliers, catalog
