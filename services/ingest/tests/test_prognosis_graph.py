"""The graph's logic, without a model and without LangGraph.

Every node is a pure function of state, and the graph is a thin wiring layer
over them, so almost all of this is exercisable with a stub `ask`. That split is
deliberate: the value of the graph is in what validation keeps, what repair
recovers, and what the explanation admits to having dropped — none of which
needs a running model to pin.

The property under test throughout is the one the single-shot path got wrong:
**nothing is discarded silently.** A factor that cannot be evaluated is either
repaired or reported, and a reviewer approving a profile can see what is not in
it.
"""

from __future__ import annotations

import json

import pytest
from app.prognosis_graph import (
    MAX_REPAIR_ROUNDS,
    GraphState,
    Rejection,
    describe_vocabulary,
    explanation_payload,
    fallback_explanation,
    make_nodes,
    merge_repaired,
    needs_repair,
    repair_payload,
    split_sections,
    validate_factor,
    validate_risks,
)

LABEL = (
    "[boxed_warning]\n"
    "Lactic acidosis has occurred. Risk factors include renal impairment and age "
    "65 years or greater.\n\n"
    "[geriatric_use]\n"
    "Elderly patients are at greater risk of hypoglycaemia."
)

QUOTE = "Risk factors include renal impairment and age 65 years or greater."
GERIATRIC_QUOTE = "Elderly patients are at greater risk of hypoglycaemia."


def risk(**kw) -> dict:
    base = {
        "reaction": "lactic acidosis",
        "seriousness": "fatal",
        "risk_factors": [{"feature": "egfr_band", "op": "at_or_below", "value": "30-44"}],
        "section": "boxed_warning",
        "citation": QUOTE,
    }
    return {**base, **kw}


# --- sections -----------------------------------------------------------------


def test_sections_are_split_on_the_markers_label_text_writes():
    sections = split_sections(LABEL)
    assert [name for name, _ in sections] == ["boxed_warning", "geriatric_use"]
    assert QUOTE in sections[0][1]


def test_text_without_markers_is_still_extractable():
    """A label that does not use the markers must not silently yield nothing —
    that is indistinguishable from a drug with no conditional risk."""
    sections = split_sections("Some prose with no section header.")
    assert len(sections) == 1
    assert sections[0][1].startswith("Some prose")


def test_empty_sections_are_dropped():
    assert split_sections("[warnings]\n\n[boxed_warning]\ntext") == [("boxed_warning", "text")]


# --- validation partitions, it does not prune ---------------------------------


def test_a_good_factor_passes():
    assert validate_factor({"feature": "age_band", "op": "eq", "value": "65-74"}) is None


def test_an_unknown_feature_is_named_in_the_reason():
    reason = validate_factor({"feature": "bmi", "op": "eq", "value": "30"})
    assert reason and "bmi" in reason


def test_an_out_of_vocabulary_value_lists_what_is_allowed():
    """This is the exact failure the graph exists to recover: the label says
    'moderate to severe renal impairment' and the model answers with a word
    instead of a band."""
    reason = validate_factor({"feature": "egfr_band", "op": "at_or_below", "value": "moderate"})
    assert reason
    assert "moderate" in reason
    assert "30-44" in reason, "the reason must carry the allowed values, for the repair round"


def test_a_rejected_factor_is_reported_not_dropped():
    bad = risk(risk_factors=[{"feature": "bmi", "op": "eq", "value": "30"}])
    partition = validate_risks([bad], LABEL)
    assert partition.kept == []
    assert partition.rejected, "the factor vanished with no record — the old behaviour"
    assert any("bmi" in r.reason for r in partition.rejected)


def test_a_risk_keeps_its_good_factors_and_reports_the_bad_one():
    mixed = risk(
        risk_factors=[
            {"feature": "egfr_band", "op": "at_or_below", "value": "30-44"},
            {"feature": "bmi", "op": "eq", "value": "30"},
        ]
    )
    partition = validate_risks([mixed], LABEL)
    assert len(partition.kept) == 1
    assert len(partition.kept[0]["risk_factors"]) == 1
    assert len(partition.rejected) == 1


def test_a_risk_with_no_surviving_factor_is_rejected_whole():
    """With no conditions left it would match every patient and flag the entire
    cohort — worse than not having it."""
    partition = validate_risks(
        [risk(risk_factors=[{"feature": "bmi", "op": "eq", "value": "30"}])], LABEL
    )
    assert partition.kept == []


def test_an_unquoted_risk_is_rejected_whole():
    """Unlike analogue, where a bad quote is blanked and the recommendation
    stands, a prognosis with no reviewable basis is not a prognosis."""
    partition = validate_risks([risk(citation="The label definitely says this.")], LABEL)
    assert partition.kept == []
    assert any("verbatim" in r.reason for r in partition.rejected)


def test_a_clean_extraction_needs_no_repair():
    partition = validate_risks([risk()], LABEL)
    assert partition.kept and partition.clean


# --- repair -------------------------------------------------------------------


def test_the_vocabulary_is_generated_from_the_feature_table():
    """Written out by hand it would drift from PROGNOSIS_FEATURES, and the model
    would be asked to use values the validator rejects."""
    described = describe_vocabulary()
    assert "egfr_band" in described and "30-44" in described
    assert "at_or_below" in described


def test_repair_asks_only_about_the_rejects():
    """Re-sending everything would invite the model to reconsider factors that
    already passed, which is a second opinion, not a repair."""
    rejected = [Rejection({"feature": "bmi", "op": "eq", "value": "30"}, "'bmi' is not collected")]
    payload = repair_payload("861007", rejected, LABEL)
    assert "bmi" in payload["rejected"]
    assert "is not collected" in payload["rejected"], "the reason has to reach the model"
    assert "lactic acidosis" not in payload["rejected"]


def test_a_repaired_factor_is_folded_into_the_existing_reaction():
    kept = [risk()]
    repaired = [
        risk(risk_factors=[{"feature": "age_band", "op": "at_or_above", "value": "65-74"}])
    ]
    merged = merge_repaired(kept, repaired, LABEL)
    assert len(merged.kept) == 1, "a repair must not create a duplicate reaction row"
    features = {f["feature"] for f in merged.kept[0]["risk_factors"]}
    assert features == {"egfr_band", "age_band"}


def test_a_repair_that_is_still_invalid_is_rejected_again():
    repaired = [risk(risk_factors=[{"feature": "bmi", "op": "eq", "value": "still wrong"}])]
    merged = merge_repaired([], repaired, LABEL)
    assert merged.kept == []
    assert merged.rejected


def test_repair_runs_once_at_most():
    """A model that cannot use the vocabulary after being shown it will not on
    the third try — it will invent something that passes."""
    state: GraphState = {
        "rejected": [{"factor": {"feature": "bmi"}, "reason": "no", "reaction": "x"}],
        "repair_rounds": MAX_REPAIR_ROUNDS,
    }
    assert needs_repair(state) == "explain"


def test_repair_is_skipped_when_nothing_is_repairable():
    """A rejection with no factor — a bad citation, say — cannot be repaired by
    re-expressing anything, so it must not cost a call."""
    state: GraphState = {"rejected": [{"factor": {}, "reason": "bad quote", "reaction": "x"}]}
    assert needs_repair(state) == "explain"


def test_repair_runs_when_a_factor_can_be_re_expressed():
    state: GraphState = {"rejected": [{"factor": {"feature": "bmi"}, "reason": "no", "reaction": "x"}]}
    assert needs_repair(state) == "repair"


# --- the explanation stage ----------------------------------------------------


def test_the_explanation_is_not_shown_the_label():
    """It describes an extraction that already happened. Given the label it
    could add a claim the extraction never made, and a reviewer would have no
    way to tell which sentences came from where."""
    payload = explanation_payload({"rxcui": "861007", "kept": [risk()], "rejected": []})
    assert "source_text" not in payload
    assert QUOTE not in json.dumps(payload)


def test_the_explanation_payload_carries_what_was_dropped():
    dropped = [{"factor": {"feature": "bmi"}, "reason": "not collected", "reaction": "acidosis"}]
    payload = explanation_payload({"rxcui": "1", "kept": [], "rejected": dropped})
    assert "not collected" in payload["dropped"]


def test_the_fallback_lists_what_was_dropped():
    """The reviewer's gate must not depend on a model being reachable, and the
    thing they most need is the part that is missing from what they approve."""
    text = fallback_explanation(
        {
            "rxcui": "861007",
            "drug_name": "RxCUI 861007",
            "kept": [risk()],
            "rejected": [{"factor": {"feature": "bmi"}, "reason": "not collected", "reaction": "acidosis"}],
        }
    )
    assert "lactic acidosis" in text
    assert "not collected" in text
    assert "dropped" in text.lower()


def test_the_fallback_says_so_even_with_nothing_kept():
    text = fallback_explanation({"rxcui": "1", "kept": [], "rejected": []})
    assert "0 risk profile" in text


# --- nodes, against a stub model ----------------------------------------------


def _stub(answers: dict[str, dict]):
    """An `ask_ai`-shaped callable returning canned answers per task."""
    calls: list[tuple[str, dict]] = []

    def ask(task: str, payload: dict) -> dict:
        calls.append((task, payload))
        if task not in answers:
            raise RuntimeError(f"model unavailable for {task}")
        return answers[task]

    ask.calls = calls  # type: ignore[attr-defined]
    return ask


def test_extract_calls_once_per_section():
    ask = _stub({"prognosis_section": {"risks": [risk()]}})
    nodes = make_nodes(ask)
    out = nodes["extract"]({"rxcui": "1", "sections": split_sections(LABEL)})
    assert len(ask.calls) == 2, "one call per section, not one for the whole label"
    assert len(out["risks"]) == 2


def test_a_failing_section_does_not_end_the_label():
    """A boxed warning that extracts is worth having even if another section
    times out."""
    ask = _stub({})  # every call raises
    nodes = make_nodes(ask)
    out = nodes["extract"]({"rxcui": "1", "sections": split_sections(LABEL)})
    assert out["risks"] == []


def test_a_failing_section_is_recorded_not_swallowed():
    """A reviewer looking at three profiles cannot otherwise tell whether the
    fourth section was clean or simply never read."""
    nodes = make_nodes(_stub({}))
    out = nodes["extract"]({"rxcui": "1", "sections": split_sections(LABEL)})
    assert out["failed_sections"] == ["boxed_warning", "geriatric_use"]


def test_the_reviewer_is_told_a_section_was_never_read():
    text = fallback_explanation(
        {"rxcui": "1", "kept": [], "rejected": [], "failed_sections": ["boxed_warning"]}
    )
    assert "boxed_warning" in text
    assert "missing" in text.lower()


def test_a_repaired_factor_is_flagged_for_the_reviewer():
    """A model under correction fits the vocabulary rather than abstaining, so
    these are the factors that most need a second look."""
    repaired = [risk(risk_factors=[{"feature": "age_band", "op": "eq", "value": "65-74"}])]
    merged = merge_repaired([], repaired, LABEL)
    assert merged.kept[0]["risk_factors"][0]["repaired"] is True


def test_the_explanation_calls_out_repaired_factors():
    kept = merge_repaired(
        [], [risk(risk_factors=[{"feature": "age_band", "op": "eq", "value": "65-74"}])], LABEL
    ).kept
    text = fallback_explanation({"rxcui": "1", "kept": kept, "rejected": []})
    assert "re-expressed" in text


def test_the_deterministic_account_is_the_explanation_not_a_fallback():
    """Fluent prose next to an approval button raises approval rates. What the
    reviewer must read is the account that is true by construction."""
    nodes = make_nodes(_stub({"prognosis_explain": {"explanation": "Looks fine to me."}}))
    out = nodes["explain"]({"rxcui": "1", "kept": [risk()], "rejected": []})
    assert "lactic acidosis" in out["explanation"], "the model's prose must not replace it"
    assert out["explanation_note"] == "Looks fine to me."


def test_explain_falls_back_when_the_model_is_unavailable():
    nodes = make_nodes(_stub({}))
    out = nodes["explain"]({"rxcui": "1", "kept": [risk()], "rejected": []})
    assert "lactic acidosis" in out["explanation"]


def test_a_blank_model_answer_leaves_the_account_intact():
    nodes = make_nodes(_stub({"prognosis_explain": {"explanation": "   "}}))
    out = nodes["explain"]({"rxcui": "1", "kept": [risk()], "rejected": []})
    assert "lactic acidosis" in out["explanation"]
    assert out["explanation_note"] == ""


def test_a_failed_repair_keeps_the_first_pass():
    nodes = make_nodes(_stub({}))
    state: GraphState = {
        "rxcui": "1",
        "sections": split_sections(LABEL),
        "kept": [risk()],
        "rejected": [{"factor": {"feature": "bmi"}, "reason": "no", "reaction": "acidosis"}],
    }
    out = nodes["repair"](state)
    assert out["repair_rounds"] == 1
    assert "kept" not in out or out["kept"] == [risk()]


# --- the wiring ---------------------------------------------------------------


def test_the_graph_compiles():
    """Thin, but it is the one thing the pure tests above cannot cover: that the
    nodes and edges actually form a graph LangGraph accepts."""
    pytest.importorskip("langgraph")
    from app.prognosis_graph import build_graph

    assert build_graph(ask=_stub({})) is not None


def test_the_graph_runs_end_to_end():
    pytest.importorskip("langgraph")
    from app.prognosis_graph import build_graph

    ask = _stub(
        {
            "prognosis_section": {"risks": [risk()]},
            "prognosis_explain": {"explanation": "One profile kept, nothing dropped."},
        }
    )
    final = build_graph(ask=ask).invoke(
        {"rxcui": "861007", "drug_name": "RxCUI 861007", "sections": split_sections(LABEL)}
    )
    assert final["kept"]
    assert final["explanation"]
