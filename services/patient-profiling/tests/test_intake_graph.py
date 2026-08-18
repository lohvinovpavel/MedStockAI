"""Patient document intake, without a model.

The prompts are the easy half. What decides whether this is safe is what happens
to a value after the model returns it: an eGFR misread off a photograph, a lab
older than the record, a number that disagrees with the one already stored. Each
of those has a wrong answer that looks exactly like a right one on screen, so
they are pinned here rather than left to a prompt.
"""

from __future__ import annotations

import pytest
from app.intake_graph import (
    EGFR_CONFLICT_DELTA,
    Extracted,
    make_nodes,
    normalise,
    normalise_egfr,
    normalise_hepatic,
    normalise_sex,
    reconcile,
)

# --- reading what the page says -----------------------------------------------


@pytest.mark.parametrize(
    "printed,expected",
    [
        ("32", 32.0),
        ("32.4", 32.4),
        ("  47 mL/min/1.73m2 ", 47.0),
        ("60", 60.0),
    ],
)
def test_an_egfr_is_read_off_the_page(printed, expected):
    value, _why = normalise_egfr(printed)
    assert value == expected


def test_a_floor_value_is_taken_as_the_floor():
    """Reports print >60 rather than a number. The conservative reading of a
    floor is the floor: assuming 90 would invent kidney function."""
    value, why = normalise_egfr(">60")
    assert value == 60.0
    assert "floor" in why


@pytest.mark.parametrize("printed", ["320", "0", "-5", "1800"])
def test_an_implausible_egfr_is_rejected_not_clamped(printed):
    """A photographed report transposes digits. 320 is a misread 32, and
    clamping it to 180 would still be a wrong kidney reading as a measurement."""
    value, why = normalise_egfr(printed)
    assert value is None
    assert why


@pytest.mark.parametrize("printed", ["", None, "pending", "see comment"])
def test_an_unreadable_egfr_is_rejected(printed):
    assert normalise_egfr(printed)[0] is None


@pytest.mark.parametrize(
    "printed,expected",
    [
        ("normal", "normal"),
        ("impaired", "impaired"),
        ("moderate hepatic impairment", "impaired"),
        ("cirrhosis", "impaired"),
        ("hepatic insufficiency", "impaired"),
        ("unremarkable", "normal"),
    ],
)
def test_hepatic_wording_maps_to_the_three_states(printed, expected):
    assert normalise_hepatic(printed)[0] == expected


def test_hepatic_wording_it_cannot_map_is_rejected():
    """Guessing here sets a 20-point finding from a phrase nobody checked."""
    assert normalise_hepatic("see hepatology note")[0] is None


@pytest.mark.parametrize("printed,expected", [("F", "F"), ("female", "F"), ("M", "M")])
def test_sex_is_normalised(printed, expected):
    assert normalise_sex(printed)[0] == expected


def test_a_field_the_ruleset_does_not_read_is_rejected():
    """Extraction must not widen the record. A field nothing scores on is a
    field nobody validates."""
    outcome = normalise([{"field": "blood_pressure", "value": "140/90"}], "lab_report:2026-01-01")
    assert outcome.applied == []
    assert outcome.rejected


def test_coded_fields_pass_through_as_lists():
    outcome = normalise(
        [{"field": "prior_adr_rxcuis", "value": ["29046"]}], "discharge_summary:2026-01-01"
    )
    assert outcome.applied[0].value == ["29046"]


def test_the_source_is_carried_for_provenance():
    """/explain has to be able to say a finding rests on a value read off a
    page rather than typed by a clinician."""
    outcome = normalise([{"field": "egfr_value", "value": "45"}], "lab_report:2026-08-01")
    assert outcome.applied[0].as_provenance() == "lab_report:2026-08-01"


# --- reconciling with what is already known -----------------------------------


def test_an_empty_field_is_filled():
    outcome = reconcile([Extracted("egfr_value", 45.0, "lab:2026")], existing={})
    assert outcome.applied and not outcome.conflicts


def test_an_older_document_never_overwrites_a_newer_value():
    """A 2023 creatinine replacing a 2026 one is a patient getting healthier on
    paper only."""
    outcome = reconcile(
        [Extracted("egfr_value", 90.0, "lab:2023")],
        existing={"egfr_value": 32.0},
        document_date="2023-01-01",
        existing_date="2026-08-01",
    )
    assert outcome.applied == []
    assert any("older" in why for _f, why in outcome.rejected)


def test_a_newer_value_is_applied():
    outcome = reconcile(
        [Extracted("egfr_value", 28.0, "lab:2026-08")],
        existing={"egfr_value": 30.0},
        document_date="2026-08-01",
        existing_date="2026-01-01",
    )
    assert outcome.applied


def test_a_large_move_is_applied_and_raised():
    """Applied because it is the newer measurement; raised because renal
    function moving that far is a dose conversation, not a silent update."""
    outcome = reconcile(
        [Extracted("egfr_value", 30.0, "lab:2026-08")],
        existing={"egfr_value": 30.0 + EGFR_CONFLICT_DELTA + 5},
        document_date="2026-08-01",
        existing_date="2026-01-01",
    )
    assert outcome.applied, "the newer measurement still wins"
    assert outcome.conflicts, "but a clinician has to see it"


def test_a_small_move_is_applied_quietly():
    """Ordinary variation between draws should not page anyone."""
    outcome = reconcile(
        [Extracted("egfr_value", 46.0, "lab:2026-08")],
        existing={"egfr_value": 45.0},
        document_date="2026-08-01",
        existing_date="2026-01-01",
    )
    assert outcome.applied and not outcome.conflicts


def test_a_changed_categorical_is_flagged():
    outcome = reconcile(
        [Extracted("hepatic", "impaired", "summary:2026-08")],
        existing={"hepatic": "normal"},
    )
    assert outcome.conflicts


def test_an_undated_document_still_applies_to_an_empty_field():
    """Most of the value is in filling gaps, and a missing date must not block
    that — it only decides overwrites."""
    outcome = reconcile([Extracted("hepatic", "impaired", "note:undated")], existing={})
    assert outcome.applied


# --- the flow -----------------------------------------------------------------


def _stub(answers):
    def ask(task, payload, *a, **kw):
        if task not in answers:
            raise RuntimeError(f"unavailable: {task}")
        return answers[task]

    return ask


def test_a_lab_report_routes_to_the_lab_prompt():
    """A lab panel and a discharge summary are not the same question; one
    prompt for both is how a creatinine gets read as a bilirubin."""
    seen = []

    def ask(task, payload, *a, **kw):
        seen.append(task)
        return {"kind": "lab_report", "features": []}

    nodes = make_nodes(ask)
    nodes["extract"]({"kind": "lab_report", "document_text": "x"})
    assert seen == ["patient_doc_labs"]


def test_an_unclassified_document_still_gets_read():
    nodes = make_nodes(_stub({}))
    assert nodes["classify"]({"document_text": "x"}) == {"kind": "unknown"}


def test_a_failed_extraction_yields_nothing_rather_than_raising():
    nodes = make_nodes(_stub({}))
    assert nodes["extract"]({"kind": "lab_report"}) == {"raw": []}


def test_the_graph_runs_end_to_end():
    pytest.importorskip("langgraph")
    from app.intake_graph import build_graph

    ask = _stub(
        {
            "patient_doc_classify": {"kind": "lab_report", "document_date": "2026-08-01"},
            "patient_doc_labs": {
                "features": [
                    {"field": "egfr_value", "value": "32", "quote": "eGFR 32"},
                    {"field": "egfr_value", "value": "320", "quote": "misread"},
                ]
            },
        }
    )
    final = build_graph(ask=ask).invoke(
        {"patient_ref": "p1", "document_text": "eGFR 32", "existing": {}}
    )
    assert [e.value for e in final["extracted"]] == [32.0]
    assert final["rejected"], "the implausible one is reported, not silently dropped"
