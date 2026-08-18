"""`computed_at` has to move when a re-check moves, and it did not.

The Re-check button exists so a pharmacist who has just read a recall notice
does not have to wait out a seven-day TTL. It calls `POST /explore`, which
re-fetches upstream and upserts. That worked -- findings were replaced and the
TTL was extended -- but the row still reported the time of the very first
check, so the dialog said "Checked 12:05" after a check at 12:31.

The cause is a trap worth naming, because the codebase has now hit it twice
(see `DrugRiskProfile.extracted_at`): `onupdate=func.now()` is applied by
SQLAlchemy when *it* emits an UPDATE. `INSERT .. ON CONFLICT DO UPDATE` is an
insert as far as Core is concerned, and the conflict branch is the database's
business, so `onupdate` never fires. The column silently keeps its first value
for the life of the row.

For the daily feed the consequence is worse than a wrong tooltip: a frozen
timestamp is indistinguishable from a feed that quietly stopped running, which
is the exact failure this field exists to make visible.
"""

from __future__ import annotations

from medstock_shared.models import AdrSignal, DrugCertification


def _column(model, name):
    return model.__table__.columns[name]


def test_certification_freshness_has_no_onupdate():
    """Pinned deliberately. Re-adding `onupdate` here looks like a fix and is a
    regression: it would read as a guarantee the write path cannot honour."""
    assert _column(DrugCertification, "computed_at").onupdate is None


def test_adr_signal_freshness_has_no_onupdate():
    """Same column definition, same upsert, same trap -- Tier 1 ratios would
    report the date of the first FAERS run forever."""
    assert _column(AdrSignal, "computed_at").onupdate is None


def test_the_on_demand_path_stamps_it_explicitly():
    """The other half of the fix. Dropping `onupdate` without this would leave
    the stamp on its server_default and never advance it at all.

    The scheduled feed's half is pinned in
    services/ingest/tests/test_certification_mapping.py."""
    import inspect

    from app import explore as on_demand

    assert '"computed_at": now' in inspect.getsource(on_demand.explore)
