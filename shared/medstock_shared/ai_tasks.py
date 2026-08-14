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
    # Seconds, when this task needs longer than settings.llm_timeout_seconds.
    # Only for offline work: a request-path task that needs 90 s does not need a
    # bigger timeout, it needs to not be on the request path.
    timeout_seconds: float | None = None


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


# --- PP-3 prognosis (docs/prognosis-and-procurement.md) ----------------------
# The model may only describe patients in terms we actually collect. Anything
# outside this vocabulary is unevaluable at request time, so it is dropped
# rather than stored as a rule nobody can apply.
PROGNOSIS_FEATURES: dict[str, set[str] | None] = {
    "age_band": {"18-39", "40-64", "65-74", "75-89", "90+"},
    "egfr_band": {">=90", "60-89", "45-59", "30-44", "15-29", "<15"},
    "hepatic": {"normal", "impaired", "unknown"},
    "sex": {"F", "M"},
    "weight_kg_band": None,      # free-form band, not enumerated yet
    "allergy_codes": None,       # open vocabulary by nature
    "condition_codes": None,     # ICD-10
    "active_rxcuis": None,       # RxCUI
    "prior_adr_rxcuis": None,
}
PROGNOSIS_OPS = {"eq", "in", "has", "at_or_below", "at_or_above"}


def _prognosis_is_applicable(result: dict) -> None:
    """Drop risk factors we could never evaluate, and quotes that were not said.

    Same philosophy as `_citation_must_be_verbatim`: prune rather than reject.
    One bad factor in a five-factor profile should not throw away the other
    four, but nothing unverifiable may survive into a table a pharmacist reads.

    A risk with no surviving factors is removed entirely — it would otherwise
    match every patient and flag the whole cohort.
    """
    source = result.get("source_text", "")
    risks = result.get("risks")
    if not isinstance(risks, list):
        result["risks"] = []
        return

    kept: list[dict] = []
    for risk in risks:
        if not isinstance(risk, dict) or not risk.get("reaction"):
            continue

        quote = risk.get("citation", "")
        if not quote or quote not in source:
            # Unlike analogue, a prognosis without a citation has no reviewable
            # basis at all, so it is dropped rather than blanked.
            continue

        factors = []
        for factor in risk.get("risk_factors") or []:
            if not isinstance(factor, dict):
                continue
            feature, op, value = factor.get("feature"), factor.get("op"), factor.get("value")
            if feature not in PROGNOSIS_FEATURES or op not in PROGNOSIS_OPS:
                continue
            allowed = PROGNOSIS_FEATURES[feature]
            if allowed is not None:
                values = value if isinstance(value, list) else [value]
                if not values or any(v not in allowed for v in values):
                    continue
            factors.append({"feature": feature, "op": op, "value": value})

        if factors:
            risk["risk_factors"] = factors
            kept.append(risk)

    result["risks"] = kept


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
    "prognosis": AITask(
        name="prognosis",
        owner="Andrii",
        prompt=(
            "You are reading an FDA drug label for {drug_name} (RxCUI {rxcui}).\n\n"
            "Extract adverse reactions that the label says are MORE LIKELY in patients "
            "with particular characteristics. Ignore reactions stated without a patient "
            "qualifier — a reaction everyone may have predicts nothing about anyone.\n\n"
            "Describe patients ONLY with these features and operators:\n"
            "  age_band: 18-39 | 40-64 | 65-74 | 75-89 | 90+\n"
            "  egfr_band: >=90 | 60-89 | 45-59 | 30-44 | 15-29 | <15   (kidney function)\n"
            "  hepatic: normal | impaired | unknown\n"
            "  sex: F | M\n"
            "  condition_codes: ICD-10 codes\n"
            "  active_rxcuis: RxCUI of a concomitant drug\n"
            "  prior_adr_rxcuis: RxCUI of a drug previously reacted to\n"
            "  ops: eq | in | has | at_or_below | at_or_above\n"
            "Use at_or_below for kidney function to mean 'this band or worse'.\n"
            "If the label names a risk factor you cannot express in these terms, omit "
            "that factor. Do not invent a feature name.\n\n"
            "Return JSON: "
            '{{"source_text": str, "risks": [{{"reaction": str, '
            '"seriousness": "fatal"|"serious"|"moderate", '
            '"risk_factors": [{{"feature": str, "op": str, "value": str|list}}], '
            '"section": str, "citation": str}}]}}\n'
            "Copy source_text from the Label text section unchanged. Every citation must "
            "be a verbatim sentence from it.\n\n"
            "Label text: {source_text}"
        ),
        validate=_prognosis_is_applicable,
        # Measured: reading a boxed warning and emitting structured factors takes
        # far longer than ranking a candidate list, and 20 s times out on every
        # label. This is offline work in a CronJob, so nobody is waiting.
        timeout_seconds=120.0,
    ),
    # prediction — Mykhailo
    # compliance (extract) — Andrii
}
