"""The model-backed organ placement (organ_infer.py).

Its job is to place an effect the substring tables miss, but only ever on an
organ the figure can draw, and never to fail loudly: a model outage or an
off-list answer must read as "could not place", the same blank the caller shows
when the deterministic tables come up empty.
"""

from __future__ import annotations

import medstock_shared.ai.core as ai_core
from medstock_shared.ai.core import AIError
from medstock_shared.organ_infer import infer_organs_for_effect
from medstock_shared.organs import ORGANS


def test_a_valid_answer_is_returned_filtered_to_drawable_organs(monkeypatch):
    monkeypatch.setattr(
        ai_core, "ask_ai", lambda *a, **k: {"organs": ["skin", "lungs"]}
    )
    assert infer_organs_for_effect("stevens-johnson syndrome") == ("skin", "lungs")


def test_off_list_organs_are_dropped(monkeypatch):
    """The model can only place effects on organs the figure has an anchor for.
    A part outside ORGANS is discarded, not drawn."""
    monkeypatch.setattr(
        ai_core, "ask_ai", lambda *a, **k: {"organs": ["pineal_gland", "liver"]}
    )
    assert infer_organs_for_effect("odd effect") == ("liver",)


def test_duplicates_are_collapsed(monkeypatch):
    monkeypatch.setattr(ai_core, "ask_ai", lambda *a, **k: {"organs": ["liver", "liver"]})
    assert infer_organs_for_effect("hepatotoxicity") == ("liver",)


def test_an_empty_answer_is_no_placement(monkeypatch):
    monkeypatch.setattr(ai_core, "ask_ai", lambda *a, **k: {"organs": []})
    assert infer_organs_for_effect("unplaceable") == ()


def test_a_malformed_answer_is_no_placement(monkeypatch):
    monkeypatch.setattr(ai_core, "ask_ai", lambda *a, **k: {"organs": "liver"})
    assert infer_organs_for_effect("effect") == ()


def test_a_model_failure_degrades_to_no_placement(monkeypatch):
    def _fail(*a, **k):
        raise AIError("breaker open")

    monkeypatch.setattr(ai_core, "ask_ai", _fail)
    assert infer_organs_for_effect("effect") == ()


def test_a_blank_effect_never_asks_the_model(monkeypatch):
    monkeypatch.setattr(
        ai_core, "ask_ai", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked"))
    )
    assert infer_organs_for_effect("   ") == ()


def test_every_returned_organ_is_drawable(monkeypatch):
    """Whatever the model says, the result is a subset of ORGANS."""
    monkeypatch.setattr(
        ai_core, "ask_ai", lambda *a, **k: {"organs": list(ORGANS) + ["nonsense"]}
    )
    assert set(infer_organs_for_effect("everything")) <= set(ORGANS)
