"""Resolve a drug's therapeutic class before assess() runs.

`class_of()` answers from two deterministic sources -- the curated `DRUG_CLASS`
map and the RxNorm ingredient-stem table (`class_from_ingredients`). Both are
small on purpose. When both miss, the assessment emits `DRUG_CLASS_UNKNOWN` and
silently skips every class-gated stage (allergy hard gate, interactions, organ
limits, age rules), which is why an unclassified drug shades no organs and reads
as "class rules skipped" on the figure.

This module adds a third tier: ask the model to place the drug in a class the
ruleset already knows. It stays honest about the deterministic-downstream claim:

  * curated map wins -- `register_drug_class` never overwrites a `DRUG_CLASS`
    entry, so a deliberate decision is never replaced by a guessed one;
  * the model can only pick from `ALLOWED_DRUG_CLASSES` (an off-list answer is
    dropped to "unknown"), so it cannot invent a label no rule can act on;
  * the answer is memoised in `ai_cache` by `ask_ai`, so the model runs at most
    once per novel rxcui and every later assessment of it is a cache hit.

The LLM import is local to the one function that needs it, so the pure rules
layer (`patient.py`, `organs.py`) never pulls in the AI/db stack.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from .auth import Principal
from .organs import DUPLICATE_CLASS_ORGANS
from .patient import (
    DRUG_CLASS,
    INGREDIENT_CLASS_STEMS,
    class_from_ingredients,
    class_of,
    register_drug_class,
)
from .rxnorm import RxNormError, ingredients_for_rxcui

_log = logging.getLogger(__name__)

# Every class label the ruleset recognises: the curated map's values, the
# ingredient-stem table's classes, and the classes a duplicate shades an organ
# for. Derived from those tables rather than hand-listed, so a class added to
# any of them is automatically a valid answer here and nothing drifts.
ALLOWED_DRUG_CLASSES: frozenset[str] = frozenset(
    set(DRUG_CLASS.values())
    | {cls for _stem, cls in INGREDIENT_CLASS_STEMS}
    | set(DUPLICATE_CLASS_ORGANS)
)


def _names_from_ingredients(ingredients: Iterable[dict]) -> list[str]:
    return [str(i.get("name") or "") for i in ingredients if i.get("name")]


def classify_drug_class_llm(
    rxcui: str,
    ingredient_names: list[str],
    *,
    principal: Principal | None = None,
) -> str | None:
    """Ask the model to place a drug in one known class.

    Returns the label (one of `ALLOWED_DRUG_CLASSES`) or None -- for an empty
    ingredient list, any AI failure (timeout, 429, open breaker), or an answer
    outside the allowed set. None means "still unknown", exactly as before the
    model was consulted; the caller degrades, it does not error.
    """
    # Local import keeps the rules layer free of the AI/db stack.
    from .ai.core import AIError, ask_ai

    names = [n for n in ingredient_names if n]
    if not names:
        return None
    try:
        result = ask_ai(
            "drug_class",
            {
                "drug_name": names[0],
                # Deduped, order-preserving: the same key every time this drug is
                # seen, so the ai_cache entry is reused instead of re-charged.
                "ingredients": ", ".join(dict.fromkeys(names)),
                "allowed": ", ".join(sorted(ALLOWED_DRUG_CLASSES)),
            },
            principal=principal,
        )
    except AIError as exc:
        _log.info("drug_class LLM unavailable for rxcui %s: %s", rxcui, exc)
        return None

    label = str(result.get("drug_class") or "").strip().lower()
    if label in ALLOWED_DRUG_CLASSES:
        return label
    # "unknown" or an off-list guess: leave the drug where it was.
    return None


def ensure_drug_class(
    rxcui: str,
    *,
    ingredient_names: list[str] | None = None,
    principal: Principal | None = None,
    allow_llm: bool = True,
) -> str | None:
    """Resolve and register a drug's class before `assess()` runs.

    Three tiers, first hit wins: the curated map / already-resolved cache
    (`class_of`), then the RxNorm ingredient stems, then -- when `allow_llm` --
    the model. A derived class is registered so `assess()` and `class_of()` both
    see it. Returns the class or None; a None still surfaces as
    `DRUG_CLASS_UNKNOWN` downstream, unchanged.

    Pass `ingredient_names` when the caller already fetched them (the cart path
    needs them for avoid-warnings anyway) to avoid a second RxNorm round-trip.
    """
    rx = str(rxcui).strip()
    existing = class_of(rx)
    if existing:
        return existing

    names = list(ingredient_names) if ingredient_names is not None else None
    if names is None:
        try:
            names = _names_from_ingredients(ingredients_for_rxcui(rx))
        except RxNormError:
            names = []

    stem_cls = class_from_ingredients(names)
    if stem_cls:
        register_drug_class(rx, stem_cls)
        return stem_cls

    if allow_llm:
        llm_cls = classify_drug_class_llm(rx, names, principal=principal)
        if llm_cls:
            register_drug_class(rx, llm_cls)
            return llm_cls

    return None
