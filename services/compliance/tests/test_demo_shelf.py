"""The dashboard shelf and the seeded shelf must hold the same NDCs.

COMP-1 only reaches the screen if all three agree on the key:

    web/lib/mock-data.ts                    the NDC the badge asks about
    shared/medstock_shared/demo_shelf.py    the NDC that lands in stock_snapshot
    shelf_ndcs()                            what the ingest-certification CronJob certifies

Drift between the first two is silent and total: the daily job certifies drugs
nobody can see, every badge on the dashboard reads "unknown", and nothing
anywhere errors. Editing one list without the other is the obvious way in, so
this pins them together.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_MOCK_DATA = _ROOT / "web" / "lib" / "mock-data.ts"
_SEED = _ROOT / "shared" / "medstock_shared" / "demo_shelf.py"

# `ndc:` at the inventory-row indent, so an NDC appearing in some other
# structure later cannot quietly satisfy this.
_UI_NDC = re.compile(r'^    ndc: "(\d{11})"', re.MULTILINE)
_SEED_NDC = re.compile(r'"ndc": "(\d{11})"')


def _ui_ndcs() -> list[str]:
    return _UI_NDC.findall(_MOCK_DATA.read_text(encoding="utf-8"))


def _seed_ndcs() -> list[str]:
    return _SEED_NDC.findall(_SEED.read_text(encoding="utf-8"))


def test_shelf_sources_exist():
    assert _MOCK_DATA.is_file(), f"missing {_MOCK_DATA}"
    assert _SEED.is_file(), f"missing {_SEED}"


def test_ui_shelf_is_not_empty():
    """A regex that quietly matches nothing would make every other assertion
    here pass by comparing two empty lists."""
    assert len(_ui_ndcs()) >= 10


def test_seeded_shelf_matches_the_dashboard():
    ui, seeded = sorted(_ui_ndcs()), sorted(_seed_ndcs())
    missing = set(ui) - set(seeded)
    extra = set(seeded) - set(ui)
    assert not missing, f"on the dashboard but never seeded (badge reads unknown): {sorted(missing)}"
    assert not extra, f"seeded but not on the dashboard: {sorted(extra)}"
    assert ui == seeded


def test_shelf_ndcs_are_canonical_11_digit():
    """certification is keyed on the 11-digit form. A hyphenated NDC here would
    miss every lookup while looking perfectly correct on the page."""
    for ndc in _ui_ndcs() + _seed_ndcs():
        assert ndc.isdigit(), ndc
        assert len(ndc) == 11, ndc


def test_no_duplicate_ndcs_on_the_shelf():
    ui = _ui_ndcs()
    assert len(ui) == len(set(ui)), "the same NDC appears on two dashboard rows"


_UI_ROW = re.compile(
    r'id: "(inv-\d+)"[\s\S]*?'
    r'drugName: "([^"]+)"[\s\S]*?'
    r'ndc: "(\d{11})"[\s\S]*?'
    r'batchNumber: "([^"]+)"[\s\S]*?'
    r'currentStock: (\d+)[\s\S]*?'
    r'expiryDate: iso\(addDays\(today, (\d+)\)\)',
)


def test_seeded_shelf_copies_mock_qty_lot_and_expiry():
    """Wave 2 inventory reads Postgres. If quantity/lot/expiry drift from the
    mock table, the demo story (critical ceftriaxone, 8-day expiry, named lots)
    silently becomes a different product."""
    from medstock_shared.demo_shelf import DASHBOARD_SHELF
    from medstock_shared.stock import derive_status

    mock = {
        ndc: {"id": iid, "name": name, "lot": lot, "quantity": int(qty), "expiry_days": int(days)}
        for iid, name, ndc, lot, qty, days in _UI_ROW.findall(_MOCK_DATA.read_text(encoding="utf-8"))
    }
    assert len(mock) == len(DASHBOARD_SHELF)
    critical = []
    for item in DASHBOARD_SHELF:
        expected = mock[item["ndc"]]
        assert item["id"] == expected["id"], item["ndc"]
        assert item["name"] == expected["name"], item["ndc"]
        assert item["lot"] == expected["lot"], item["ndc"]
        assert item["quantity"] == expected["quantity"], item["ndc"]
        assert item["expiry_days"] == expected["expiry_days"], item["ndc"]
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


def test_facility_profile_matches_mock_inventory_for():
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

    # The script writes both tables, so check the names against both rather than
    # against whichever one happens to be first.
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
