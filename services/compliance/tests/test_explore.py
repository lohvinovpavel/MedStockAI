"""COMP-2 on-demand exploration. Upstreams are stubbed — these pin the rules and
the decision to explore, not openFDA's uptime.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from medstock_shared.certification import Category, Severity, Status, evaluate, status_for
from medstock_shared.ndc_status import NdcStatus, fetch_ndc_status

OBSOLETE = NdcStatus(status="OBSOLETE", start_date="201007", end_date="201206")
ACTIVE = NdcStatus(status="ACTIVE", start_date="200706", end_date="202608")


def codes(findings) -> set[str]:
    return {f.code for f in findings}


# --- what RxNorm can conclude ----------------------------------------------


def test_an_obsolete_ndc_is_red():
    """The scheduled feed can never produce this: openFDA's directory has
    already dropped the product, so only RxNorm still knows it existed."""
    findings = evaluate(ndc_status=OBSOLETE, in_directory=False)
    assert status_for(findings) is Status.RED
    assert "NDC_OBSOLETE" in codes(findings)


def test_obsolete_beats_being_absent_from_the_directory():
    findings = evaluate(ndc_status=OBSOLETE, in_directory=False)
    assert "NDC_ACTIVE_UNLISTED" not in codes(findings)


def test_active_but_unlisted_is_green_and_says_why():
    """Green here means 'a government source vouches for it', not 'we found
    nothing' — so the reason is recorded even though it costs no severity."""
    findings = evaluate(ndc_status=ACTIVE, in_directory=False)
    assert status_for(findings) is Status.GREEN
    assert "NDC_ACTIVE_UNLISTED" in codes(findings)
    assert all(f.severity is Severity.INFO for f in findings)


def test_nothing_recognised_the_ndc_at_all():
    findings = evaluate(ndc_status=None, in_directory=False)
    assert "NDC_UNRESOLVED" in codes(findings)
    assert status_for(findings) is Status.GREEN


def test_a_drug_found_in_the_directory_does_not_get_the_unlisted_note():
    findings = evaluate(ndc_status=ACTIVE, in_directory=True, marketing_end_date=date(2030, 1, 1))
    assert "NDC_ACTIVE_UNLISTED" not in codes(findings)
    assert "NDC_UNRESOLVED" not in codes(findings)


def test_dates_unknown_only_applies_to_records_we_actually_have():
    """An NDC absent from the directory has no dates by definition; saying so
    would be noise on top of NDC_UNRESOLVED."""
    assert "DATES_UNKNOWN" not in codes(evaluate(in_directory=False))
    assert "DATES_UNKNOWN" in codes(evaluate(in_directory=True))


def test_rxnorm_findings_are_categorised_and_sourced():
    for f in evaluate(ndc_status=OBSOLETE, in_directory=False):
        assert f.category is Category.LIFECYCLE
        assert "RxNorm" in f.source
        assert f.source_url


@pytest.mark.parametrize("status", ["obsolete", "OBSOLETE", " Obsolete "])
def test_status_matching_is_case_and_space_insensitive(status):
    findings = evaluate(ndc_status=NdcStatus(status=status), in_directory=False)
    assert "NDC_OBSOLETE" in codes(findings)


def test_an_unrecognised_rxnorm_status_is_not_invented_into_a_verdict():
    findings = evaluate(ndc_status=NdcStatus(status="ALIEN"), in_directory=False)
    assert "NDC_OBSOLETE" not in codes(findings)
    assert "NDC_ACTIVE_UNLISTED" not in codes(findings)


# --- the client -------------------------------------------------------------


def test_fetch_returns_none_when_rxnorm_says_nothing(monkeypatch):
    monkeypatch.setattr("medstock_shared.ndc_status._get", lambda ndc: {"ndcStatus": {}})
    assert fetch_ndc_status("00000000000") is None


def test_fetch_parses_history(monkeypatch):
    monkeypatch.setattr(
        "medstock_shared.ndc_status._get",
        lambda ndc: {
            "ndcStatus": {
                "status": "OBSOLETE",
                "ndcHistory": [
                    {"startDate": "201007", "endDate": "201206", "activeRxcui": "995258"}
                ],
            }
        },
    )
    result = fetch_ndc_status("68071010030")
    assert result.is_obsolete and not result.is_active
    assert (result.start_date, result.end_date, result.active_rxcui) == (
        "201007", "201206", "995258",
    )


def test_a_network_failure_is_absence_not_an_exception(monkeypatch):
    import httpx

    def boom(ndc):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr("medstock_shared.ndc_status._get", boom)
    assert fetch_ndc_status("68071010030") is None


# --- staleness --------------------------------------------------------------


def test_scheduled_rows_never_go_stale():
    """They have a CronJob behind them; only on-demand rows expire."""
    from app.explore import is_stale

    class Row:
        expires_at = None

    assert is_stale(Row()) is False


def test_an_expired_on_demand_row_is_stale():
    from app.explore import is_stale

    class Row:
        expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)

    assert is_stale(Row()) is True


def test_a_fresh_on_demand_row_is_not():
    from app.explore import TTL_DAYS, is_stale

    class Row:
        expires_at = datetime.now(tz=UTC) + timedelta(days=TTL_DAYS)

    assert is_stale(Row()) is False
