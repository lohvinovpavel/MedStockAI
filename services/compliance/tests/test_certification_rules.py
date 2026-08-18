"""COMP-1 colour rules. Pure functions, no database, no network — the whole
point of deriving the traffic light deterministically is that it can be pinned
down exactly like this.

`today` is always passed explicitly. A test that depends on the wall clock
starts failing on its own schedule.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from medstock_shared.certification import (
    YELLOW_EXPIRY_WINDOW_DAYS,
    AlertListing,
    Finding,
    NewsItem,
    Recall,
    Severity,
    Shortage,
    Status,
    attention_for,
    evaluate,
    ndc11,
    parse_fda_date,
    product_ndc_candidates,
    ruleset,
    signal,
    status_for,
)

TODAY = date(2026, 8, 14)


def codes(findings) -> set[str]:
    return {f.code for f in findings}


def colour(**kwargs) -> Status:
    return status_for(evaluate(today=TODAY, **kwargs))


# --- dates ------------------------------------------------------------------


# --- NDC formats -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0093-9222-05", "00093922205"),  # 4-4-2, the common published form
        ("00093-922-05", "00093092205"),  # 5-3-2 — pads the *product* segment
        ("0904-2015-59", "00904201559"),  # verified live against openFDA
        ("0093-9222", "000939222"),  # product NDC, no package segment
        ("00093922205", "00093922205"),  # already canonical
    ],
)
def test_ndc11_pads_to_5_4_2(raw, expected):
    assert ndc11(raw) == expected


def test_different_hyphenations_are_different_drugs():
    """`0093-9222-05` and `00093-922-05` look alike and are not the same NDC.
    Padding the wrong segment silently points a badge at another product."""
    assert ndc11("0093-9222-05") != ndc11("00093-922-05")


def test_candidates_cover_every_hyphenation_the_padding_erased():
    """11 digits is 5-4-2, but the published original may have been 4-4-2,
    5-3-2 or 5-4-1 — nothing in the digits says which."""
    assert product_ndc_candidates("00093922205") == ["0093-9222", "00093-9222"]
    assert product_ndc_candidates("00113041178") == ["0113-0411", "00113-411", "00113-0411"]


def test_candidates_round_trip_through_ndc11():
    for candidate in product_ndc_candidates("00904201559"):
        assert ndc11(f"{candidate}-59") == "00904201559"


def test_non_canonical_input_is_returned_untouched():
    assert product_ndc_candidates("not-an-ndc") == ["not-an-ndc"]


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
    published = set(ruleset()["rules"])
    emitted = codes(
        evaluate(
            listing_expiration_date=date(2020, 1, 1),
            marketing_end_date=date(2026, 9, 1),
            marketing_start_date=date(2027, 1, 1),
            marketing_category="UNAPPROVED DRUG OTHER",
            finished=False,
            recalls=[
                Recall(classification="Class I", status="Ongoing"),
                Recall(classification="Class II", status="Ongoing"),
                Recall(classification="Class III", status="Ongoing"),
            ],
            shortages=[
                Shortage(status="Current"),
                Shortage(status="To Be Discontinued"),
            ],
            today=TODAY,
        )
    ) | {"DATES_UNKNOWN", "MARKETING_ENDED"}
    assert emitted <= published


def test_every_published_rule_is_reachable():
    """The inverse: a documented code nobody can emit is a lie in the ruleset."""
    reachable = codes(
        evaluate(
            listing_expiration_date=date(2020, 1, 1),
            marketing_end_date=date(2026, 9, 1),
            marketing_start_date=date(2027, 1, 1),
            marketing_category="UNAPPROVED DRUG OTHER",
            finished=False,
            recalls=[
                Recall(classification=c, status="Ongoing") for c in ("Class I", "Class II", "Class III")
            ],
            shortages=[Shortage(status="Current"), Shortage(status="To Be Discontinued")],
            today=TODAY,
        )
    )
    reachable |= codes(evaluate(today=TODAY))  # DATES_UNKNOWN
    reachable |= codes(evaluate(marketing_end_date=date(2020, 1, 1), today=TODAY))
    # COMP-2's three, which only an on-demand exploration can produce.
    reachable |= codes(evaluate(in_directory=False, today=TODAY))  # NDC_UNRESOLVED
    for rxnorm_status in ("OBSOLETE", "ACTIVE"):
        reachable |= codes(
            evaluate(
                ndc_status=SimpleNamespace(status=rxnorm_status, start_date="", end_date=""),
                in_directory=False,
                today=TODAY,
            )
        )
    # Import certification (§4.1) and news (§4.2) — neither can be produced by
    # the openFDA fields above, so they need their own inputs here.
    reachable |= codes(
        evaluate(
            import_alerts=[
                AlertListing(alert_number="66-40", firm_name="A"),
                AlertListing(alert_number="66-41", firm_name="B"),
            ],
            news=[NewsItem(headline="story", url="https://x.test/1")],
            today=TODAY,
        )
    )
    assert set(ruleset()["rules"]) == reachable


# --- the detailed signal ----------------------------------------------------


def test_persistent_property_does_not_demand_attention():
    """An unapproved marketing category is permanent. It colours the badge, but
    it is not something happening now — 372 of 375 yellows in a real 3 000
    product sample were exactly this, and burying a recall under them is the
    failure mode the split exists to prevent."""
    findings = evaluate(
        marketing_end_date=date(2030, 1, 1),
        marketing_category="UNAPPROVED DRUG OTHER",
        today=TODAY,
    )
    assert status_for(findings) is Status.YELLOW
    assert attention_for(findings) is Status.GREEN


def test_an_event_does_demand_attention():
    findings = evaluate(
        marketing_end_date=date(2030, 1, 1),
        marketing_category="UNAPPROVED DRUG OTHER",
        recalls=[Recall(classification="Class II", status="Ongoing", recall_number="D-1")],
        today=TODAY,
    )
    assert attention_for(findings) is Status.YELLOW


def test_signal_groups_reasons_by_category():
    findings = evaluate(
        marketing_end_date=date(2026, 9, 1),
        marketing_category="UNAPPROVED DRUG OTHER",
        recalls=[Recall(classification="Class I", status="Ongoing", recall_number="D-1")],
        shortages=[Shortage(status="Current")],
        today=TODAY,
    )
    detail = signal(findings)
    assert detail["categories"] == {
        "lifecycle": "yellow",
        "approval": "yellow",
        "enforcement": "red",
        "supply": "yellow",
    }
    assert detail["transient"] == 3 and detail["persistent"] == 1


def test_listing_expiry_has_no_forward_window():
    """70.5% of real products share a single annual listing expiry date. A
    'expiring soon' rule on that field turns most of a formulary amber on one
    October morning, so only a lapsed listing counts."""
    soon = evaluate(listing_expiration_date=TODAY + timedelta(days=30), today=TODAY)
    assert status_for(soon) is Status.GREEN
    lapsed = evaluate(listing_expiration_date=TODAY - timedelta(days=1), today=TODAY)
    assert status_for(lapsed) is Status.RED


@pytest.mark.parametrize(
    "status,expected",
    [("Current", Status.YELLOW), ("To Be Discontinued", Status.YELLOW), ("Resolved", Status.GREEN)],
)
def test_shortage_statuses(status, expected):
    assert (
        status_for(
            evaluate(
                marketing_end_date=date(2030, 1, 1),
                shortages=[Shortage(status=status)],
                today=TODAY,
            )
        )
        is expected
    )


def test_bulk_ingredient_is_informational_only():
    findings = evaluate(marketing_end_date=date(2030, 1, 1), finished=False, today=TODAY)
    assert status_for(findings) is Status.GREEN
    assert "NOT_FINISHED_PRODUCT" in codes(findings)


def test_a_retired_rule_code_does_not_break_rendering():
    """Stored rows outlive the ruleset that wrote them."""
    stale = Finding(code="LISTING_EXPIRING_SOON", message="", source="")
    assert stale.severity is Severity.INFO
    assert signal([stale])["status"] == "green"
