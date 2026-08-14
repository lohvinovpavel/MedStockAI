"""COMP-1 colour rules. Pure functions, no database, no network — the whole
point of deriving the traffic light deterministically is that it can be pinned
down exactly like this.

`today` is always passed explicitly. A test that depends on the wall clock
starts failing on its own schedule.
"""

from datetime import date

import pytest

from medstock_shared.certification import (
    YELLOW_EXPIRY_WINDOW_DAYS,
    Recall,
    Severity,
    Status,
    evaluate,
    parse_fda_date,
    ruleset,
    status_for,
)

TODAY = date(2026, 8, 14)


def codes(findings) -> set[str]:
    return {f.code for f in findings}


def colour(**kwargs) -> Status:
    return status_for(evaluate(today=TODAY, **kwargs))


# --- dates ------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("20260814", date(2026, 8, 14)),
        ("2026-08-14", date(2026, 8, 14)),
        ("", None),
        (None, None),
        ("not-a-date", None),
        ("20260231", None),  # 31 February — malformed, not an exception
    ],
)
def test_parse_fda_date(raw, expected):
    assert parse_fda_date(raw) == expected


# --- red --------------------------------------------------------------------


def test_expired_listing_is_red():
    findings = evaluate(listing_expiration_date=date(2026, 1, 1), today=TODAY)
    assert status_for(findings) is Status.RED
    assert "LISTING_EXPIRED" in codes(findings)


def test_expired_marketing_is_red():
    assert colour(marketing_end_date=date(2025, 12, 31)) is Status.RED


def test_ongoing_class_i_recall_is_red():
    findings = evaluate(
        marketing_end_date=date(2030, 1, 1),
        recalls=[Recall(classification="Class I", status="Ongoing", recall_number="D-123")],
        today=TODAY,
    )
    assert status_for(findings) is Status.RED
    assert "RECALL_CLASS_I" in codes(findings)


# --- yellow -----------------------------------------------------------------


def test_expiring_inside_the_window_is_yellow():
    soon = date(2026, 9, 1)  # 18 days out
    findings = evaluate(marketing_end_date=soon, today=TODAY)
    assert status_for(findings) is Status.YELLOW
    assert "MARKETING_ENDING_SOON" in codes(findings)


@pytest.mark.parametrize(
    "days,expected",
    [
        (0, Status.YELLOW),  # expires today — not yet expired
        (YELLOW_EXPIRY_WINDOW_DAYS, Status.YELLOW),  # last day inside the window
        (YELLOW_EXPIRY_WINDOW_DAYS + 1, Status.GREEN),  # first day outside it
    ],
)
def test_window_boundaries(days, expected):
    from datetime import timedelta

    assert colour(marketing_end_date=TODAY + timedelta(days=days)) is expected


@pytest.mark.parametrize("classification", ["Class II", "Class III"])
def test_lesser_recalls_are_yellow(classification):
    assert (
        colour(
            marketing_end_date=date(2030, 1, 1),
            recalls=[Recall(classification=classification, status="Ongoing")],
        )
        is Status.YELLOW
    )


def test_unapproved_marketing_category_is_yellow():
    findings = evaluate(
        marketing_end_date=date(2030, 1, 1),
        marketing_category="UNAPPROVED DRUG OTHER",
        today=TODAY,
    )
    assert status_for(findings) is Status.YELLOW
    assert "UNAPPROVED_CATEGORY" in codes(findings)


def test_approved_category_produces_nothing():
    assert (
        colour(marketing_end_date=date(2030, 1, 1), marketing_category="ANDA") is Status.GREEN
    )


# --- green, and what green is allowed to mean -------------------------------


def test_clean_record_is_green():
    findings = evaluate(
        marketing_end_date=date(2030, 1, 1),
        listing_expiration_date=date(2030, 1, 1),
        marketing_category="NDA",
        today=TODAY,
    )
    assert status_for(findings) is Status.GREEN
    assert findings == []


def test_terminated_recall_is_not_a_signal():
    assert (
        colour(
            marketing_end_date=date(2030, 1, 1),
            recalls=[Recall(classification="Class I", status="Terminated")],
        )
        is Status.GREEN
    )


def test_missing_dates_stay_green_but_say_so():
    """Green must never quietly mean 'we had no data'."""
    findings = evaluate(today=TODAY)
    assert status_for(findings) is Status.GREEN
    assert "DATES_UNKNOWN" in codes(findings)
    assert all(f.severity is Severity.INFO for f in findings)


def test_info_alone_does_not_downgrade():
    assert status_for(evaluate(today=TODAY)) is Status.GREEN


# --- precedence -------------------------------------------------------------


def test_red_outranks_yellow_regardless_of_order():
    findings = evaluate(
        listing_expiration_date=date(2020, 1, 1),  # red
        marketing_end_date=date(2026, 9, 1),  # yellow
        marketing_category="UNAPPROVED DRUG OTHER",  # yellow
        today=TODAY,
    )
    assert status_for(findings) is Status.RED
    assert {"LISTING_EXPIRED", "MARKETING_ENDING_SOON", "UNAPPROVED_CATEGORY"} <= codes(findings)


def test_every_finding_names_its_source():
    findings = evaluate(
        listing_expiration_date=date(2020, 1, 1),
        recalls=[Recall(classification="Class I", status="Ongoing", recall_number="D-9")],
        today=TODAY,
    )
    assert all(f.source and f.source_url for f in findings)


def test_two_recalls_stay_distinct_under_the_natural_key():
    """`(ndc, code, source_ref)` is the upsert key — two Class II recalls on one
    drug must not collapse into a single finding."""
    findings = evaluate(
        marketing_end_date=date(2030, 1, 1),
        recalls=[
            Recall(classification="Class II", status="Ongoing", recall_number="D-1"),
            Recall(classification="Class II", status="Ongoing", recall_number="D-2"),
        ],
        today=TODAY,
    )
    refs = {f.source_ref for f in findings if f.code == "RECALL_CLASS_II"}
    assert refs == {"D-1", "D-2"}


# --- the published ruleset --------------------------------------------------


def test_ruleset_documents_every_code_the_rules_can_emit():
    published = set(ruleset()["red"]) | set(ruleset()["yellow"]) | set(ruleset()["info"])
    emitted = codes(
        evaluate(
            listing_expiration_date=date(2020, 1, 1),
            marketing_end_date=date(2026, 9, 1),
            marketing_category="UNAPPROVED DRUG OTHER",
            recalls=[
                Recall(classification="Class I", status="Ongoing"),
                Recall(classification="Class II", status="Ongoing"),
                Recall(classification="Class III", status="Ongoing"),
            ],
            today=TODAY,
        )
    ) | {"DATES_UNKNOWN", "LISTING_EXPIRING_SOON", "MARKETING_ENDED"}
    assert emitted <= published
