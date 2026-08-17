"""The dashboard shelf and the seeded shelf must hold the same NDCs.

COMP-1 only reaches the screen if all three agree on the key:

    web/lib/mock-data.ts   the NDC the badge asks about
    scripts/seed_stock.py  the NDC that lands in stock_snapshot
    shelf_ndcs()           what the ingest-certification CronJob certifies

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
_SEED = _ROOT / "scripts" / "seed_stock.py"

# `ndc:` at the inventory-row indent, so an NDC appearing in some other
# structure later cannot quietly satisfy this.
_UI_NDC = re.compile(r'^    ndc: "(\d{11})"', re.MULTILINE)
_SEED_NDC = re.compile(r'\{"ndc": "(\d{11})"')


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
