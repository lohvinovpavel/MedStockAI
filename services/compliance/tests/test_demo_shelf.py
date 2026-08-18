"""The dashboard shelf seed must stay internally consistent.

COMP-1 only reaches the screen if seed NDCs, lots, quantities and the
certification CronJob agree. Editing DASHBOARD_SHELF without the rest of
`demo_shelf.py` is the obvious way in, so this pins the story SKUs.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SEED = _ROOT / "shared" / "medstock_shared" / "demo_shelf.py"

_SEED_NDC = re.compile(r'"ndc": "(\d{11})"')


def _seed_ndcs() -> list[str]:
    return _SEED_NDC.findall(_SEED.read_text(encoding="utf-8"))


def test_shelf_sources_exist():
    assert _SEED.is_file(), f"missing {_SEED}"


def test_seeded_shelf_is_not_empty():
    from medstock_shared.demo_shelf import DASHBOARD_SHELF

    assert len(DASHBOARD_SHELF) >= 10
    assert len(_seed_ndcs()) >= 10


def test_seeded_ndcs_match_dashboard_shelf():
    from medstock_shared.demo_shelf import DASHBOARD_SHELF

    declared = [item["ndc"] for item in DASHBOARD_SHELF]
    assert _seed_ndcs() == declared


def test_shelf_ndcs_are_canonical_11_digit():
    from medstock_shared.demo_shelf import DASHBOARD_SHELF

    for item in DASHBOARD_SHELF:
        ndc = item["ndc"]
        assert ndc.isdigit(), ndc
        assert len(ndc) == 11, ndc


def test_no_duplicate_ndcs_on_the_shelf():
    from medstock_shared.demo_shelf import DASHBOARD_SHELF

    ndcs = [item["ndc"] for item in DASHBOARD_SHELF]
    assert len(ndcs) == len(set(ndcs)), "the same NDC appears on two dashboard rows"


def test_seeded_shelf_copies_qty_lot_and_expiry():
    """Wave 2 inventory reads Postgres. If quantity/lot/expiry drift, the demo
    story (critical ceftriaxone, 8-day expiry, named lots) silently becomes a
    different product."""
    from medstock_shared.demo_shelf import DASHBOARD_SHELF
    from medstock_shared.stock import derive_status

    critical = []
    for item in DASHBOARD_SHELF:
        assert item["lot"]
        assert item["quantity"] >= 0
        assert item["expiry_days"] > 0
        status, par = derive_status(item["quantity"], item["par_reorder"], item["par_target"])
        assert par is True
        if status == "critical":
            critical.append(item["name"])
    assert critical == [
        "Ceftriaxone 1g",
        "Norepinephrine 4mg/4mL",
        "Insulin Glargine 100U/mL",
        "Heparin Sodium 5000IU/mL",
    ]
    by_name = {item["name"]: item for item in DASHBOARD_SHELF}
    assert by_name["Ceftriaxone 1g"]["quantity"] == 9
    assert by_name["Ceftriaxone 1g"]["lot"] == "CFX-25011-A"
    assert by_name["Ceftriaxone 1g"]["expiry_days"] == 8
    assert by_name["Norepinephrine 4mg/4mL"]["quantity"] == 6


def test_facility_profile_matches_inventory_for():
    from medstock_shared.demo_shelf import FACILITY_SHELF_PROFILE, lot_for

    assert FACILITY_SHELF_PROFILE["riverside"]["absent"] == ("inv-002", "inv-005")
    assert FACILITY_SHELF_PROFILE["westend"]["absent"] == ("inv-002", "inv-005", "inv-008")
    assert FACILITY_SHELF_PROFILE["warehouse-north"]["absent"] == ("inv-007",)
    assert FACILITY_SHELF_PROFILE["riverside"]["stock_factor"] == 0.35
    assert FACILITY_SHELF_PROFILE["westend"]["stock_factor"] == 0.22
    assert FACILITY_SHELF_PROFILE["warehouse-north"]["stock_factor"] == 7.0
    assert lot_for({"lot": "AMX-24118-B"}, "central") == "AMX-24118-B"
    assert lot_for({"lot": "AMX-24118-B"}, "riverside") == "AMX-24118-B-DE"


def test_dashboard_shelf_has_wave3_rxcuis_and_shortage_specs():
    from medstock_shared.demo_shelf import DASHBOARD_SHELF, DEMO_SHORTAGE_SPECS, formulary_rxcuis

    assert all(item.get("rxcui") for item in DASHBOARD_SHELF)
    assert len(formulary_rxcuis()) == len(DASHBOARD_SHELF)
    ids = {item["id"] for item in DASHBOARD_SHELF}
    assert {s["id"] for s in DEMO_SHORTAGE_SPECS} == {"inv-003", "inv-005", "inv-010"}
    assert all(s["id"] in ids for s in DEMO_SHORTAGE_SPECS)


def test_wave4_partner_stock_and_suppliers():
    from medstock_shared.demo_shelf import DEMO_SUPPLIERS, PARTNER_SHORTAGE_STOCK

    assert set(PARTNER_SHORTAGE_STOCK) == {"inv-003", "inv-005", "inv-010"}
    assert PARTNER_SHORTAGE_STOCK["inv-005"]["st-luke"]["units"] == 0
    assert PARTNER_SHORTAGE_STOCK["inv-003"]["st-luke"]["units"] == 210
    assert "mercy" not in PARTNER_SHORTAGE_STOCK["inv-003"]
    names = [row["name"] for row in DEMO_SUPPLIERS]
    assert names == [
        "PharmaSource Global Ltd.",
        "Meditech Distribution Co.",
        "EuroPharm Wholesale AG",
        "Nordic Medical Supply",
    ]
    assert DEMO_SUPPLIERS[0]["catalog"]["inv-005"] == "22.5000"


def test_wave5_demo_orders_keep_legacy_po_refs():
    from medstock_shared.demo_shelf import DEMO_ORDERS

    refs = [row["ref"] for row in DEMO_ORDERS]
    assert "PO-2026-0148" in refs
    assert "PO-2026-0141" in refs
    assert all(row["facility"] in {
        "central", "riverside", "westend", "warehouse-north", "st-luke", "mercy",
    } for row in DEMO_ORDERS)


def test_the_seed_targets_a_constraint_that_still_exists():
    """`seed_stock.py` names a constraint in its ON CONFLICT clause, and a
    migration renamed it out from under the script.

    20260817_warehouse added `facility_id` to the natural key — location codes
    repeat across facilities, every clinic has a "fridge-1" — dropping
    `uq_stock_hospital_ndc_loc` for `uq_stock_hospital_ndc_fac_loc`. The script
    kept naming the old one and raised UndefinedObject against any database at
    head, which is every environment docs/populating-a-new-environment.md tells
    you to seed. Nothing caught it because no test ran the script against a
    migrated database.

    Pinning the name against the model is cheaper than that round trip and fails
    on the rename rather than on the next person's first deploy.
    """
    import re

    from medstock_shared.models import FormularyItem, ParLevel, StockBatch, StockSnapshot

    named = re.findall(r'constraint="([^"]+)"', (_ROOT / "scripts" / "seed_stock.py").read_text(encoding="utf-8"))
    assert named, "seed_stock.py should pin its upserts to named constraints"

    declared = {
        c.name
        for model in (StockSnapshot, FormularyItem, StockBatch, ParLevel)
        for c in model.__table__.constraints
        if c.name
    }
    unknown = [c for c in named if c not in declared]
    assert not unknown, (
        f"seed_stock.py upserts on {unknown}, which no seeded model declares. "
        f"Declared: {sorted(declared)}"
    )
