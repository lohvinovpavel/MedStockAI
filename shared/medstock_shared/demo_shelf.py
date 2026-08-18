"""NDCs the inventory dashboard shows (web/lib/mock-data.ts).

One list so seed_stock, seed_demo and COMP-1 cannot drift. Warehouse needs
`drug` + `stock_snapshot` + a consumption series for these or the picker
shows unnamed rows with empty charts. `storage_class` places each SKU on
the right shelf (insulin in a fridge, not the main room).

Wave 2: `quantity` / `lot` / `expiry_days` are the mock inventory row.
`par_reorder` / `par_target` make B5 status a real claim — the four
critical story SKUs (ceftriaxone, norepinephrine, insulin, heparin) sit
at or below reorder. Facility profiles copy `inventoryFor()` so switching
site changes depth the same way the mock table did.
"""

from __future__ import annotations

from .demo_tenant import FACILITIES, location_for

# storage_min/max and humidity match warehouse CLASS_RANGES / seed_demo drugs.csv.
# `id` matches mock-data.ts inventory ids so FACILITY_SHELF_PROFILE.absent
# can name the same rows the mock omitted per site.
DASHBOARD_SHELF: tuple[dict, ...] = (
    {
        "id": "inv-001",
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

# Mirrors web/lib/mock-data.ts FACILITY_PROFILE.stockFactor / absent.
# burnFactor stays on the mock (orders/forecasts/shortages still read it).
FACILITY_SHELF_PROFILE: dict[str, dict] = {
    "central": {"stock_factor": 1.0, "absent": ()},
    "riverside": {"stock_factor": 0.35, "absent": ("inv-002", "inv-005")},
    "westend": {"stock_factor": 0.22, "absent": ("inv-002", "inv-005", "inv-008")},
    "warehouse-north": {"stock_factor": 7.0, "absent": ("inv-007",)},
}


def lot_for(item: dict, facility_code: str) -> str:
    """Same suffix rule as mock `inventoryFor()` (`facilityId.slice(-2)`)."""
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
