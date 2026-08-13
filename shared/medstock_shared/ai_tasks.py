"""Task registry. `ai.py` owns the *mechanism* (the Gemini call, retries,
the cache). Each service owns its *task*: the prompt and the shape of the
answer, registered here rather than hardcoded in `ai.py`.

Add your task here; you do not touch `ai.py` to do it.
Owner column is not decoration — it is who gets paged when a prompt regresses.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class AITask:
    name: str
    owner: str
    prompt: str                      # str.format()-ed with the payload passed to ask_ai()
    validate: Callable[[dict], None] | None = None   # raise to reject the answer (raises AIError)


def _citation_must_be_verbatim(result: dict) -> None:
    """A hallucinated citation is the failure mode that matters clinically.
    The quote has to appear in the source text, character for character."""
    source = result.get("source_text", "")
    for item in result.get("items", []):
        quote = item.get("citation", "")
        if quote and quote not in source:
            raise ValueError(f"citation not found in source: {quote[:60]!r}")


TASKS: dict[str, AITask] = {
    "analogue": AITask(
        name="analogue",
        owner="Pavlo",
        prompt=(
            "Given the drug {drug_name} (RxCUI {rxcui}) which is in shortage, rank the "
            "therapeutic alternatives below. Return JSON: "
            '{{"items": [{{"rxcui": str, "rationale": str, "citation": str}}]}}. '
            "Every citation must be a verbatim sentence from the source text.\n\n"
            "Candidates: {candidates}\nSource text: {source_text}"
        ),
        validate=_citation_must_be_verbatim,
    ),
    # prediction — Mykhailo
    # patient-profiling — Andrii
    # compliance — Andrii
}
