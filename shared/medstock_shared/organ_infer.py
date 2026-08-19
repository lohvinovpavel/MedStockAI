"""Place a drug effect on the body when the substring tables cannot.

`organs.py` maps a named reaction or an avoided ingredient to organs by
substring (`REACTION_ORGANS`, `INGREDIENT_ORGANS`). Those tables cover the
common FAERS and label phrasings, but a rare reaction word or an ingredient with
no line draws nothing and is reported unmapped. This asks the model where the
effect acts, constrained to the organs the figure can actually draw, so the body
is shaded comprehensively rather than listing the effect off to the side.

Same guarantees as the drug-class fallback (`drug_class.py`):

  * the model may only return organs from `ORGANS` -- an off-list answer is
    dropped, so it cannot name a body part the figure has no anchor for;
  * an empty answer or any AI failure leaves the finding exactly as unmapped as
    it was, never an error;
  * `ask_ai` memoises per effect string, so the model runs at most once for each
    distinct reaction and every later figure is a cache hit.

Only the request layer calls this, and only for prose-carrying findings; the
codes deliberately mapped to "no single organ" never reach it.
"""

from __future__ import annotations

import logging

from .auth import Principal
from .organs import ORGANS

_log = logging.getLogger(__name__)

_ORGAN_SET = frozenset(ORGANS)


def infer_organs_for_effect(
    effect: str,
    *,
    principal: Principal | None = None,
) -> tuple[str, ...]:
    """Where a drug effect acts, from the model, filtered to drawable organs.

    Returns the organ tuple, or `()` for an empty effect, an empty/……off-list
    answer, or any AI failure. `()` means "still could not place it", the same
    as before the model was asked -- the caller keeps the finding unmapped.
    """
    # Local import keeps the rules layer free of the AI/db stack.
    from .ai.core import AIError, ask_ai

    text = (effect or "").strip()
    if not text:
        return ()
    try:
        result = ask_ai(
            "organ_impact",
            {"effect": text, "allowed": ", ".join(ORGANS)},
            principal=principal,
        )
    except AIError as exc:
        _log.info("organ_impact LLM unavailable for %r: %s", text, exc)
        return ()

    raw = result.get("organs")
    if not isinstance(raw, list):
        return ()
    seen: list[str] = []
    for item in raw:
        organ = str(item).strip().lower()
        if organ in _ORGAN_SET and organ not in seen:
            seen.append(organ)
    return tuple(seen)
