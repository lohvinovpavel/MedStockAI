"""NDCs the inventory dashboard shows (web/lib/mock-data.ts).

One list so seed_stock, seed_demo and COMP-1 cannot drift. Warehouse needs
`drug` + `stock_snapshot` + a consumption series for these or the picker
shows unnamed rows with empty charts. `storage_class` places each SKU on
the right shelf (insulin in a fridge, not the main room).
"""

from __future__ import annotations

# storage_min/max and humidity match warehouse CLASS_RANGES / seed_demo drugs.csv.
DASHBOARD_SHELF: tuple[dict, ...] = (
    {
        "ndc": "62135009120",
        "name": "Amoxicillin/Clavulanate 875mg",
        "quantity": 900,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
    },
    {
        "ndc": "16714097720",
        "name": "Propofol 1% Emulsion",
        "quantity": 250,
        "storage_class": "refrigerated",
        "storage_min_c": 2.0,
        "storage_max_c": 8.0,
        "humidity_max_pct": 75.0,
    },
    {
        "ndc": "82804006601",
        "name": "Ceftriaxone 1g",
        "quantity": 9,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
    },
    {
        "ndc": "00487990130",
        "name": "Salbutamol 100mcg Inhaler",
        "quantity": 140,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
    },
    {
        "ndc": "00338011220",
        "name": "Norepinephrine 4mg/4mL",
        "quantity": 60,
        "storage_class": "refrigerated",
        "storage_min_c": 2.0,
        "storage_max_c": 8.0,
        "humidity_max_pct": 75.0,
    },
    {
        "ndc": "00069406101",
        "name": "Azithromycin 250mg",
        "quantity": 420,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
    },
    {
        "ndc": "00024586900",
        "name": "Insulin Glargine 100U/mL",
        "quantity": 75,
        "storage_class": "refrigerated",
        "storage_min_c": 2.0,
        "storage_max_c": 8.0,
        "humidity_max_pct": 75.0,
    },
    {
        "ndc": "63323041125",
        "name": "Midazolam 5mg/mL",
        "quantity": 180,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
    },
    {
        "ndc": "00143938610",
        "name": "Paracetamol 1g IV",
        "quantity": 300,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
    },
    {
        "ndc": "00338043304",
        "name": "Heparin Sodium 5000IU/mL",
        "quantity": 95,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
    },
    {
        "ndc": "76168080030",
        "name": "Carmellose Sodium 0.5% Eye Drops",
        "quantity": 62,
        "storage_class": "crt",
        "storage_min_c": 15.0,
        "storage_max_c": 25.0,
        "humidity_max_pct": 60.0,
    },
)
