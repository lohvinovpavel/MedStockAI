"""Smoke check for the natural-key mapping each feed upserts on — the part
that stays stable once the real field names replace today's placeholders."""

from app.pricing import _row_to_values as pricing_values
from app.rxnorm import _row_to_values as rxnorm_values
from app.shortages import _row_to_values as shortages_values


def test_shortages_natural_key_is_stable():
    row = {"shortage_id": "abc123", "ndc": "0002-1433", "status": "current"}
    assert shortages_values(row)["source_id"] == "abc123"


def test_shortages_falls_back_to_ndc_when_no_id():
    row = {"ndc": "0002-1433", "status": "resolved"}
    assert shortages_values(row)["source_id"] == "0002-1433"


def test_pricing_natural_key_is_ndc_and_date():
    row = {"ndc": "0002-1433", "effective_date": "2026-08-01", "nadac_per_unit": "1.23"}
    values = pricing_values(row)
    assert (values["ndc"], values["effective_date"]) == ("0002-1433", "2026-08-01")


def test_rxnorm_natural_key_is_the_edge():
    values = rxnorm_values("161", {"rxcui": "161", "tty": "IN"})
    assert (values["rxcui_from"], values["rxcui_to"], values["relationship"]) == ("161", "161", "IN")
