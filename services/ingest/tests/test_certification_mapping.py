"""The natural keys and field mapping the certification feed upserts on — the
part that must stay stable once the real openFDA field names are verified."""

from datetime import date

from app.certification import _ndcs_of, _product_to_values
from medstock_shared.certification import Recall

TODAY = date(2026, 8, 14)


def test_natural_key_is_the_product_ndc():
    certification, _ = _product_to_values({"product_ndc": "0002-1433"}, [], TODAY)
    assert certification["ndc"] == "0002-1433"


def test_record_without_an_ndc_is_skipped():
    assert _product_to_values({"labeler_name": "Acme"}, [], TODAY) is None


def test_status_is_derived_not_copied():
    certification, findings = _product_to_values(
        {"product_ndc": "0002-1433", "listing_expiration_date": "20250101"}, [], TODAY
    )
    assert certification["status"] == "red"
    assert [f["code"] for f in findings] == ["LISTING_EXPIRED"]


def test_dates_are_parsed_into_dates():
    certification, _ = _product_to_values(
        {"product_ndc": "0002-1433", "marketing_end_date": "20301231"}, [], TODAY
    )
    assert certification["marketing_end_date"] == date(2030, 12, 31)


def test_unparseable_date_does_not_fail_the_row():
    certification, findings = _product_to_values(
        {"product_ndc": "0002-1433", "marketing_end_date": "garbage"}, [], TODAY
    )
    assert certification["marketing_end_date"] is None
    assert "DATES_UNKNOWN" in {f["code"] for f in findings}


def test_recalls_reach_the_findings():
    _, findings = _product_to_values(
        {"product_ndc": "0002-1433", "marketing_end_date": "20301231"},
        [Recall(classification="Class I", status="Ongoing", recall_number="D-77")],
        TODAY,
    )
    recall_rows = [f for f in findings if f["code"] == "RECALL_CLASS_I"]
    assert recall_rows and recall_rows[0]["source_ref"] == "D-77"


def test_every_row_records_which_ruleset_produced_it():
    certification, _ = _product_to_values({"product_ndc": "0002-1433"}, [], TODAY)
    assert certification["ruleset_version"]
    assert certification["provenance"] == "scheduled"


def test_finding_rows_carry_the_ndc_for_the_upsert_key():
    _, findings = _product_to_values(
        {"product_ndc": "0002-1433", "listing_expiration_date": "20250101"}, [], TODAY
    )
    assert all(f["ndc"] == "0002-1433" for f in findings)


def test_ndcs_are_collected_from_every_place_a_recall_names_them():
    record = {
        "product_ndc": "0002-1433",
        "openfda": {"package_ndc": ["0002-1433-01"], "product_ndc": ["0002-9999"]},
    }
    assert set(_ndcs_of(record)) == {"0002-1433", "0002-1433-01", "0002-9999"}


def test_recall_without_ndcs_is_not_a_crash():
    assert _ndcs_of({"recall_number": "D-1"}) == []
