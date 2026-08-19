"""Three-tier drug-class resolution: curated map, ingredient stems, LLM.

The point of the third tier is that DRUG_CLASS_UNKNOWN stops firing on drugs a
model could obviously place -- but only within the classes the ruleset already
acts on, and only when it cannot fail loudly. So the properties that matter:

* a curated class is never replaced by a derived one;
* the deterministic stem tier is tried before the model, and the model only
  when it is allowed;
* an answer outside the known vocabulary, or any model failure, degrades to the
  same "still unknown" it would have been without the model -- never an error,
  never a shaded wrong organ.
"""

from __future__ import annotations

import medstock_shared.ai.core as ai_core
import pytest
from medstock_shared.ai.core import AIError
from medstock_shared.drug_class import (
    ALLOWED_DRUG_CLASSES,
    classify_drug_class_llm,
    ensure_drug_class,
)
from medstock_shared.patient import DRUG_CLASS, class_of


def test_a_curated_class_wins_without_touching_the_model(monkeypatch):
    """5640 is ibuprofen, curated as an nsaid. The model must not be consulted
    for a drug the curated map already rules on."""
    called = False

    def _boom(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("model consulted for a curated drug")

    monkeypatch.setattr(ai_core, "ask_ai", _boom)
    assert ensure_drug_class("5640", ingredient_names=["ibuprofen"]) == "nsaid"
    assert called is False


def test_the_stem_tier_classifies_before_the_model(monkeypatch):
    """Methylphenidate is a stimulant by its ingredient stem -- no model needed,
    and the derived class is registered so class_of() sees it afterwards."""
    monkeypatch.setattr(
        ai_core, "ask_ai", lambda *a, **k: pytest.fail("model used when a stem matched")
    )
    rxcui = "_test_mph"
    assert ensure_drug_class(rxcui, ingredient_names=["Methylphenidate hydrochloride"]) == "stimulant"
    assert class_of(rxcui) == "stimulant"


def test_the_model_places_a_drug_the_stems_miss(monkeypatch):
    """An ingredient no stem covers falls to the model; a valid label is
    registered and returned."""
    target = next(c for c in ALLOWED_DRUG_CLASSES if c == "statin")
    monkeypatch.setattr(ai_core, "ask_ai", lambda *a, **k: {"drug_class": target})
    rxcui = "_test_novel_statin"
    assert ensure_drug_class(rxcui, ingredient_names=["fictitious-statin-ingredient"]) == target
    assert class_of(rxcui) == target


def test_an_off_list_label_stays_unknown(monkeypatch):
    """The model can only pick from the classes the rules act on. A label
    outside the vocabulary is dropped, not registered, so no wrong organ is
    shaded off a hallucinated class."""
    monkeypatch.setattr(ai_core, "ask_ai", lambda *a, **k: {"drug_class": "sparkling_water"})
    rxcui = "_test_offlist"
    assert ensure_drug_class(rxcui, ingredient_names=["unclassifiable-thing"]) is None
    assert class_of(rxcui) is None


def test_a_model_failure_degrades_to_unknown(monkeypatch):
    """A timeout, a 429, an open breaker -- any AIError -- leaves the drug
    exactly as unknown as before the model was asked. The endpoint must not
    500 because Gemini is down."""
    def _fail(*a, **k):
        raise AIError("breaker open")

    monkeypatch.setattr(ai_core, "ask_ai", _fail)
    rxcui = "_test_ai_down"
    assert ensure_drug_class(rxcui, ingredient_names=["unclassifiable-thing"]) is None
    assert class_of(rxcui) is None


def test_allow_llm_false_never_reaches_the_model(monkeypatch):
    """The bulk paths pass allow_llm=False: the deterministic tiers still run,
    but a stem miss returns unknown without a per-candidate model call."""
    monkeypatch.setattr(
        ai_core, "ask_ai", lambda *a, **k: pytest.fail("model used on a deterministic-only path")
    )
    rxcui = "_test_no_llm"
    assert ensure_drug_class(rxcui, ingredient_names=["unclassifiable-thing"], allow_llm=False) is None


def test_classify_returns_none_without_ingredients(monkeypatch):
    """Nothing to ground the question on -- do not spend a call to guess from a
    bare rxcui."""
    monkeypatch.setattr(
        ai_core, "ask_ai", lambda *a, **k: pytest.fail("model asked with no ingredients")
    )
    assert classify_drug_class_llm("_test_bare", []) is None


def test_the_allowed_vocabulary_covers_the_curated_classes():
    """Every class the curated map assigns is a label the model is allowed to
    return -- otherwise the model could never agree with a curated decision."""
    assert set(DRUG_CLASS.values()) <= ALLOWED_DRUG_CLASSES
