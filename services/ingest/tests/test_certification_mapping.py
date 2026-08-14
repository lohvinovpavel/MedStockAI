"""Field mapping and natural keys for the certification feed.

Field names were verified against live openFDA responses on 2026-08-14; the
fixtures below are trimmed copies of real payloads, so a shape change upstream
shows up here rather than in production.
"""

from datetime import date

from app.certification import _ndcs_of, _product_ndcs, _product_to_rows, _recalls_for
from medstock_shared.certification import Recall, Shortage

TODAY = date(2026, 8, 14)

# Trimmed from a live api.fda.gov/drug/ndc.json record.
DIFLUNISAL = {
    "product_ndc": "0093-9222",
    "generic_name": "Diflunisal",
    "labeler_name": "Teva Pharmaceuticals USA, Inc.",
    "marketing_category": "ANDA",
    "application_number": "ANDA070868",
    "packaging": [
        {"package_ndc": "0093-9222-01"},
        {"package_ndc": "0093-9222-05"},
        {"package_ndc": "0093-9222-06"},
    ],
}


def only(product, recalls=(), shortages=None, today=TODAY):
    rows = _product_to_rows(product, list(recalls), shortages or {}, today)
    assert rows, "expected at least one row"
    return rows


def test_one_row_per_package_ndc_keyed_11_digit():
    """Inventory holds 11-digit package NDCs, so that is what the badge must be
    findable by — not the hyphenated product NDC openFDA publishes."""
    rows = only(DIFLUNISAL)
    assert [c["ndc"] for c, _ in rows] == ["00093922201", "00093922205", "00093922206"]


def test_packages_share_product_level_status():
    """Approval and recalls apply to the product, so absent a per-package signal
    every package of one product reads the same."""
    rows = only(DIFLUNISAL)
    assert len({c["status"] for c, _ in rows}) == 1


def test_a_shortage_on_one_pack_size_does_not_flag_the_others():
    """Shortages are declared per package, unlike everything else. Verified
    against the live feed: `package_ndc` is the key, not `product_ndc`."""
    rows = only(
        DIFLUNISAL,
        shortages={"00093922205": [Shortage(status="Current", generic_name="Diflunisal")]},
    )
    by_ndc = {c["ndc"]: c["status"] for c, _ in rows}
    assert by_ndc["00093922205"] == "yellow"
    assert by_ndc["00093922201"] == "green"
    assert by_ndc["00093922206"] == "green"


def test_record_without_an_ndc_is_skipped():
    assert _product_to_rows({"labeler_name": "Acme"}, [], TODAY) == []


def test_status_is_derived_not_copied():
    (cert, findings), *_ = only({**DIFLUNISAL, "listing_expiration_date": "20250101"})
    assert cert["status"] == "red"
    assert "LISTING_EXPIRED" in {f["code"] for f in findings}


def test_dates_are_parsed_into_dates():
    (cert, _), *_ = only({**DIFLUNISAL, "marketing_end_date": "20301231"})
    assert cert["marketing_end_date"] == date(2030, 12, 31)


def test_unparseable_date_does_not_fail_the_row():
    (cert, findings), *_ = only({**DIFLUNISAL, "marketing_end_date": "garbage"})
    assert cert["marketing_end_date"] is None
    assert "DATES_UNKNOWN" in {f["code"] for f in findings}


def test_labeler_and_application_number_are_carried():
    (cert, _), *_ = only(DIFLUNISAL)
    assert cert["labeler"] == "Teva Pharmaceuticals USA, Inc."
    assert cert["application_number"] == "ANDA070868"


def test_every_row_records_which_ruleset_produced_it():
    (cert, _), *_ = only(DIFLUNISAL)
    assert cert["ruleset_version"]
    assert cert["provenance"] == "scheduled"


def test_findings_carry_the_same_key_as_their_certification():
    rows = only({**DIFLUNISAL, "listing_expiration_date": "20250101"})
    for cert, findings in rows:
        assert all(f["ndc"] == cert["ndc"] for f in findings)


def test_recalls_reach_the_findings():
    rows = only(
        {**DIFLUNISAL, "marketing_end_date": "20301231"},
        [Recall(classification="Class I", status="Ongoing", recall_number="D-77")],
    )
    _, findings = rows[0]
    recall_rows = [f for f in findings if f["code"] == "RECALL_CLASS_I"]
    assert recall_rows and recall_rows[0]["source_ref"] == "D-77"


# --- how recalls name their products ---------------------------------------


def test_ndcs_are_collected_from_every_place_a_recall_names_them():
    record = {
        "product_ndc": "0002-1433",
        "openfda": {"package_ndc": ["0002-1433-01"], "product_ndc": ["0002-9999"]},
    }
    assert set(_ndcs_of(record)) == {"0002-1433", "0002-1433-01", "0002-9999"}


def test_recall_without_openfda_annotations_is_not_a_crash():
    """Verified live: 61% of ongoing recalls have `openfda: {}` and name the
    product only in free text. They cannot be joined — but must not blow up."""
    assert _ndcs_of({"recall_number": "D-1", "openfda": {}}) == []


# --- joining recalls to products -------------------------------------------


def test_product_answers_to_its_package_ndcs_too():
    assert set(_product_ndcs(DIFLUNISAL)) == {
        "0093-9222",
        "0093-9222-01",
        "0093-9222-05",
        "0093-9222-06",
    }


def test_recall_naming_only_a_package_ndc_still_attaches():
    """The regression this guards: matching on product_ndc alone drops the join
    entirely, because enforcement annotates products by package NDC."""
    index = {"0093-9222-05": [Recall(classification="Class I", status="Ongoing", recall_number="D-5")]}
    assert [r.recall_number for r in _recalls_for(DIFLUNISAL, index)] == ["D-5"]


def test_one_recall_across_several_packages_is_counted_once():
    recall = Recall(classification="Class II", status="Ongoing", recall_number="D-7")
    index = {"0093-9222-01": [recall], "0093-9222-05": [recall], "0093-9222": [recall]}
    assert len(_recalls_for(DIFLUNISAL, index)) == 1


def test_distinct_unnumbered_recalls_are_not_collapsed():
    index = {
        "0093-9222": [
            Recall(classification="Class II", status="Ongoing", reason="one"),
            Recall(classification="Class III", status="Ongoing", reason="two"),
        ]
    }
    assert len(_recalls_for(DIFLUNISAL, index)) == 2


def test_malformed_packaging_entry_is_ignored():
    product = {"product_ndc": "0002-1433", "packaging": ["not-a-dict", {"no_ndc": 1}]}
    assert _product_ndcs(product) == ["0002-1433"]
