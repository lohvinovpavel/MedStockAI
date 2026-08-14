"""NADAC row mapping. Field names verified against live responses 2026-08-14;
these pin the shape so an upstream change fails here rather than in production.
"""

from app.pricing import _row_to_values

REAL = {
    "ndc_description": "DIFLUNISAL 500 MG TABLET",
    "ndc": "00093922205",
    "nadac_per_unit": "1.17300",
    "effective_date": "2026-07-15",
    "pricing_unit": "EA",
    "classification_for_rate_setting": "G",
    "otc": "N",
}


def test_natural_key_is_ndc_and_effective_date():
    values = _row_to_values(REAL)
    assert (values["ndc"], values["effective_date"]) == ("00093922205", "2026-07-15")


def test_price_is_numeric_not_the_string_the_feed_sends():
    assert _row_to_values(REAL)["unit_price"] == 1.173


def test_the_whole_row_is_kept():
    """`$1.04 per EA` and `per ML` are not comparable, so a price comparison has
    to be able to point at pricing_unit and the classification."""
    raw = _row_to_values(REAL)["raw"]
    assert raw["pricing_unit"] == "EA"
    assert raw["classification_for_rate_setting"] == "G"


def test_a_timestamped_effective_date_is_truncated_to_a_day():
    values = _row_to_values({**REAL, "effective_date": "2026-07-15T00:00:00+00:00"})
    assert values["effective_date"] == "2026-07-15"


def test_rows_with_no_usable_key_are_skipped():
    for bad in ({**REAL, "ndc": ""}, {**REAL, "effective_date": None}):
        assert _row_to_values(bad) is None


def test_a_blank_or_unparseable_price_is_skipped_not_zeroed():
    """A zero acquisition cost would make a drug look free next to its
    alternatives — dropping the row is the honest failure."""
    for bad in ({**REAL, "nadac_per_unit": ""}, {**REAL, "nadac_per_unit": "n/a"},
                {**REAL, "nadac_per_unit": None}):
        assert _row_to_values(bad) is None
