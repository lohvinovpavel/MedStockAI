"""drug_class backfill: RxClass primary-class picking and the committed CSV."""

from __future__ import annotations

import csv

from app.demo_layout import data_dir
from medstock_shared import rxnorm


def _info(class_id: str, class_name: str, rela_source: str, min_cui: str = "111") -> dict:
    return {
        "rxclassMinConceptItem": {"classId": class_id, "className": class_name},
        "relaSource": rela_source,
        "rela": "",
        "minConcept": {"rxcui": min_cui},
    }


def test_primary_class_prefers_atc_over_va(monkeypatch):
    infos = [
        _info("VA1", "ORAL HYPOGLYCEMIC AGENTS", "VA"),
        _info("A10BA", "Biguanides", "ATC"),
    ]
    monkeypatch.setattr(rxnorm, "_rxclass_infos", lambda rxcui: infos)
    assert rxnorm.primary_class_name("test-atc-over-va") == "Biguanides"


def test_primary_class_titlecases_va_fallback(monkeypatch):
    infos = [_info("VA1", "ORAL HYPOGLYCEMIC AGENTS", "VA")]
    monkeypatch.setattr(rxnorm, "_rxclass_infos", lambda rxcui: infos)
    assert rxnorm.primary_class_name("test-va-only") == "Oral Hypoglycemic Agents"


def test_primary_class_none_when_no_usable_class(monkeypatch):
    infos = [_info("D003924", "Diabetes Mellitus", "MEDRT")]  # disease link, not a class
    monkeypatch.setattr(rxnorm, "_rxclass_infos", lambda rxcui: infos)
    assert rxnorm.primary_class_name("test-no-class") is None


def test_committed_drugs_csv_has_drug_class():
    """Seed/CI must not need live RxNav — every demo drug ships its class."""
    with (data_dir() / "drugs.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, "drugs.csv is empty"
    assert all("drug_class" in row for row in rows)
    missing = [row["ndc"] for row in rows if not row["drug_class"].strip()]
    assert not missing, f"drugs without a class: {missing}"
