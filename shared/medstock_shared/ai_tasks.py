"""Task registry. `ai.py` owns the *mechanism* (the Gemini call, retries,
the cache). Each service owns its *task*: the prompt and the shape of the
answer, registered here rather than hardcoded in `ai.py`.

Add your task here; you do not touch `ai.py` to do it.
Owner column is not decoration — it is who gets paged when a prompt regresses.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class AITask:
    name: str
    owner: str
    prompt: str                      # str.format()-ed with the payload passed to ask_ai()
    validate: Callable[[dict], None] | None = None   # raise to reject the answer (raises AIError)


def _citation_must_be_verbatim(result: dict) -> None:
    """Drop hallucinated citations rather than rejecting the whole keep-set.

    A fabricated quote must not reach a pharmacist. Raising would make
    `ask_ai` fail and analogue fall back to the unfiltered Full list, which
    looks like AI did nothing. Stripping the quote keeps the filter.
    """
    source = result.get("source_text", "")
    items = result.get("items")
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        quote = item.get("citation", "")
        if quote and quote not in source:
            item["citation"] = ""


TASKS: dict[str, AITask] = {
    "analogue": AITask(
        name="analogue",
        owner="Pavlo",
        prompt=(
            "Given the drug {drug_name} (RxCUI {rxcui}) which is in shortage, filter the "
            "therapeutic alternatives below. Keep about 5 commonly used substitutes; drop "
            "the rest. Do not invent rxcui values. Return JSON: "
            '{{"source_text": str, '
            '"items": [{{"rxcui": str, "rationale": str, "citation": str}}]}}. '
            "Copy source_text from the Source text section unchanged. Every citation must "
            "be a verbatim sentence from the source text.\n\n"
            "Candidates: {candidates}\nSource text: {source_text}"
        ),
        validate=_citation_must_be_verbatim,
    ),
    # prediction — Mykhailo
    # patient-profiling — Andrii
    # compliance — Andrii
}
