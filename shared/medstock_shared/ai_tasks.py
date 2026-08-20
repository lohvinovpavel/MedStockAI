"""Task registry. `ai/core.py` owns the *mechanism* (the Gemini call, retries,
the cache, the breaker). Each service owns its *task*: the prompt and the
shape of the answer, registered here rather than hardcoded in `ai/core.py`.

Add your task here; you do not touch `ai/core.py` to do it.
Owner column is not decoration — it is who gets paged when a prompt regresses.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class AITask:
    name: str
    owner: str
    prompt: str                      # str.format()-ed with the payload passed to ask_ai()
    # Bump on any edit to `prompt` or `validate`. It's part of the ai_cache
    # unique key (type, prompt_version, dedupe_key) -- an old answer from a
    # since-edited prompt is a stale answer, not a valid cache hit, and
    # bumping this is what makes that cache miss instead of replaying it.
    prompt_version: str = "v1"
    # Pinned per task (H2). Empty means "use settings.gemini_model at call time"
    # for tasks that have not moved yet; a model change still invalidates the
    # cache because the key includes the resolved model id.
    model: str = ""
    validate: Callable[[dict], None] | None = None   # raise to reject the answer (raises AIError)
    # Seconds, when this task needs longer than settings.llm_timeout_seconds.
    # Only for offline work: a request-path task that needs 90 s does not need a
    # bigger timeout, it needs to not be on the request path.
    timeout_seconds: float | None = None


def _organs_is_list(result: dict) -> None:
    """The organ_impact task must return a list of organ labels. Membership in
    the drawable ORGANS set is enforced by the caller (organ_infer.py) -- an
    off-list organ is dropped there, so a stray label degrades to "could not
    place" (the finding stays unmapped, as it was) rather than an AIError."""
    if not isinstance(result, dict) or not isinstance(result.get("organs"), list):
        raise TypeError("organ_impact task must return a list 'organs'")


def _drug_class_is_string(result: dict) -> None:
    """The drug_class task must return a string label. Membership in the allowed
    set is enforced by the caller (drug_class.py), not here: an off-list label is
    coerced to "unknown" there rather than failing the whole call, so a slightly
    mislabelled answer degrades to the same DRUG_CLASS_UNKNOWN it would have been
    without the model, not to an AIError."""
    if not isinstance(result, dict) or not isinstance(result.get("drug_class"), str):
        raise TypeError("drug_class task must return a string 'drug_class'")


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


# A runaway generation, not a style rule. The explanation summarises a handful
# of profiles for a reviewer; anything past this is the model writing an essay,
# and the deterministic fallback is better than an essay.
MAX_EXPLANATION_CHARS = 4_000


def _explanation_adds_no_risks(result: dict) -> None:
    """Reject an explanation that is empty or has run away.

    Be clear about what this does not do: it cannot tell whether the prose
    invented a clinical claim. String matching against free-text reaction names
    would false-positive on every paraphrase, and a validator that fires on
    correct output is worse than none.

    What makes the stage safe is not this function — it is that
    `prognosis_graph.explain_node` falls back to `fallback_explanation` whenever
    this raises. That fallback is generated from the extraction itself, so a
    rejected explanation degrades to one that is true by construction and still
    lists everything that was dropped.
    """
    text = result.get("explanation")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("explanation must be a non-empty string")
    if len(text) > MAX_EXPLANATION_CHARS:
        raise ValueError(f"explanation is {len(text)} chars, over {MAX_EXPLANATION_CHARS}")


TASKS: dict[str, AITask] = {
    "analogue": AITask(
        name="analogue",
        owner="Pavlo",
        prompt=(
            "Given the drug {drug_name} (RxCUI {rxcui}) which is in shortage, filter the "
            "therapeutic alternatives below. Keep at most 5 commonly used substitutes; "
            "drop the rest. Do not invent rxcui values. Do not copy source_text into the "
            "JSON — the caller already has it. Return only: "
            '{{"items": [{{"rxcui": str, "rationale": str, "citation": str}}]}}. '
            "items must have 1–5 entries. rationale is one short sentence. citation is a "
            "verbatim substring of the Source text, at most 80 characters.\n\n"
            "Candidates: {candidates}\nSource text: {source_text}"
        ),
        prompt_version="v2",
        model="gemini-3.5-flash-lite",
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
        prompt_version="v1",
        model="gemini-3.5-flash-lite",
        validate=_prognosis_is_applicable,
        # Measured: reading a boxed warning and emitting structured factors takes
        # far longer than ranking a candidate list, and 20 s times out on every
        # label. This is offline work in a CronJob, so nobody is waiting.
        timeout_seconds=120.0,
    ),
    # --- PP-3 as a graph (prognosis_graph.py) --------------------------------
    # The single-shot `prognosis` task above reads a whole label in one call and
    # silently loses any factor it cannot phrase in the vocabulary. These three
    # split that into section extraction, a bounded repair round, and an account
    # of what happened — which is what makes the pharmacist's approval a review
    # rather than a signature on a list.
    "prognosis_section": AITask(
        name="prognosis_section",
        owner="Andrii",
        prompt=(
            "You are reading the {section} section of the FDA label for {drug_name} "
            "(RxCUI {rxcui}).\n\n"
            "Extract adverse reactions this section says are MORE LIKELY in patients with "
            "particular characteristics. Ignore reactions stated without a patient "
            "qualifier — a reaction everyone may have predicts nothing about anyone.\n\n"
            "Describe patients ONLY with these features and values:\n"
            "  age_band: 18-39 | 40-64 | 65-74 | 75-89 | 90+\n"
            "  egfr_band: >=90 | 60-89 | 45-59 | 30-44 | 15-29 | <15   (kidney function)\n"
            "  hepatic: normal | impaired | unknown\n"
            "  sex: F | M\n"
            "  condition_codes: ICD-10 codes\n"
            "  active_rxcuis: RxCUI of a concomitant drug\n"
            "  prior_adr_rxcuis: RxCUI of a drug previously reacted to\n"
            "  ops: eq | in | has | at_or_below | at_or_above\n"
            "Use at_or_below for kidney function to mean 'this band or worse'.\n"
            "Prefer the closest allowed value to what the text says; if there is no "
            "close one, omit that factor rather than inventing a value.\n\n"
            "Return JSON: "
            '{{"source_text": str, "risks": [{{"reaction": str, '
            '"seriousness": "fatal"|"serious"|"moderate", '
            '"risk_factors": [{{"feature": str, "op": str, "value": str|list}}], '
            '"section": str, "citation": str}}]}}\n'
            "Copy source_text from the Section text unchanged. Every citation must be a "
            "verbatim sentence from it.\n\n"
            "Section text: {source_text}"
        ),
        # No validate: the graph partitions instead of pruning, because the
        # reasons are what the repair round and the reviewer both need. Running
        # _prognosis_is_applicable here would discard them first.
        timeout_seconds=120.0,
    ),
    "prognosis_repair": AITask(
        name="prognosis_repair",
        owner="Andrii",
        prompt=(
            "Risk factors you extracted from the label for RxCUI {rxcui} could not be "
            "used. Each is listed with the reason.\n\n"
            "{rejected}\n\n"
            "Re-express ONLY these, using ONLY:\n{vocabulary}\n\n"
            "If a factor genuinely cannot be expressed in that vocabulary, leave it out. "
            "An approximation that changes which patients match is worse than nothing — "
            "these are used to decide whether a drug is flagged for a specific person.\n"
            "Do not add factors that are not listed above, and do not restate ones that "
            "were accepted.\n\n"
            "Return JSON: "
            '{{"source_text": str, "risks": [{{"reaction": str, '
            '"seriousness": "fatal"|"serious"|"moderate", '
            '"risk_factors": [{{"feature": str, "op": str, "value": str|list}}], '
            '"section": str, "citation": str}}]}}\n'
            "Copy source_text from the Label text unchanged. Every citation must be a "
            "verbatim sentence from it.\n\n"
            "Label text: {source_text}"
        ),
        timeout_seconds=120.0,
    ),
    "prognosis_explain": AITask(
        name="prognosis_explain",
        owner="Andrii",
        prompt=(
            "A system extracted conditional risk profiles from the FDA label for "
            "{drug_name} (RxCUI {rxcui}). A pharmacist is about to approve or reject "
            "them and needs an account of what the extraction did.\n\n"
            "Kept:\n{kept}\n\nDropped:\n{dropped}\n\n"
            "Write a short plain-English account for that pharmacist. Say what was "
            "found and which patients each profile would flag. Then say plainly what "
            "was dropped and why, because those conditions are NOT represented in what "
            "they are approving — a profile that lost a factor now matches more "
            "patients than the label describes.\n\n"
            "Describe only what is above. You are not reading the label and must not "
            "add a clinical claim, a citation, or a risk that is not listed.\n\n"
            'Return JSON: {{"explanation": str}}'
        ),
        validate=_explanation_adds_no_risks,
    ),
    # --- patient document intake (patient-profiling/app/intake_graph.py) -----
    # The only tasks here that send PATIENT data to the model. Everything else
    # reads public labels, which is why prognosis.py can say the BAA question
    # does not arise; for these it does.
    "patient_doc_classify": AITask(
        name="patient_doc_classify",
        owner="Andrii",
        prompt=(
            "What kind of clinical document is this, and what date does it carry?\n\n"
            'Return JSON: {{"kind": "lab_report"|"discharge_summary"|"note"|"unknown", '
            '"document_date": "YYYY-MM-DD or empty"}}\n\n'
            "The date is the date of the RESULT or the discharge, not the date it was "
            "printed. If the page shows no such date return empty rather than guessing — "
            "a wrong date decides whether this overwrites a newer measurement.\n\n"
            "Document: {source_text}"
        ),
    ),
    "patient_doc_labs": AITask(
        name="patient_doc_labs",
        owner="Andrii",
        prompt=(
            "Read the values off this laboratory report.\n\n"
            "Return ONLY these fields, omitting any the report does not state:\n"
            "  egfr_value    the eGFR, exactly as printed (e.g. 32, >60)\n"
            "  hepatic       normal | impaired, from the LFT panel (ALT/AST/bilirubin/"
            "albumin). Say impaired only if the panel supports it — a single mildly "
            "raised enzyme is not impairment.\n"
            "  sex           F | M, if the report states it\n\n"
            "Transcribe, do not interpret. If a value is unclear on the page, omit it: a "
            "guessed digit in an eGFR moves a patient between renal bands and turns a "
            "dose warning on or off.\n\n"
            'Return JSON: {{"features": [{{"field": str, "value": str, "quote": str}}]}}\n'
            "quote is the text on the page the value came from.\n\n"
            "Report: {source_text}"
        ),
        timeout_seconds=90.0,
    ),
    "patient_doc_summary": AITask(
        name="patient_doc_summary",
        owner="Andrii",
        prompt=(
            "Read this clinical document for facts that affect drug safety.\n\n"
            "Return ONLY these fields, omitting any it does not state:\n"
            "  egfr_value          eGFR, if a number is given\n"
            "  hepatic             normal | impaired\n"
            "  sex                 F | M\n"
            "  prior_adr_rxcuis    RxCUIs of drugs the patient REACTED to previously. "
            "Only where the document names a reaction or intolerance — a drug simply "
            "stopped is not a reaction. Omit the field entirely if you cannot give an "
            "RxCUI; a drug name in this field is unusable.\n"
            "  condition_codes     ICD-10 codes for stated diagnoses\n\n"
            "Do not infer. A patient 'doing well on' a drug has no reaction to it, and a "
            "family history is not the patient's history.\n\n"
            'Return JSON: {{"features": [{{"field": str, "value": str|list, "quote": str}}]}}\n'
            "quote is the sentence the fact came from.\n\n"
            "Document: {source_text}"
        ),
        timeout_seconds=90.0,
    ),
    # patient-profiling — third tier of drug-class resolution (drug_class.py).
    # The curated map and the ingredient-stem table are both small on purpose;
    # when both miss, assess() emits DRUG_CLASS_UNKNOWN and skips every
    # class-gated stage. This places the drug in a class the ruleset already
    # knows -- constrained to the allowed list passed in, so the model cannot
    # invent a label no rule can act on -- and the answer is cached per drug in
    # ai_cache, so the model runs at most once for each novel rxcui.
    "drug_class": AITask(
        name="drug_class",
        owner="Andrii",
        prompt=(
            "Classify this drug into exactly one therapeutic class, for a rules "
            "engine that shades affected organs and checks interactions.\n\n"
            "Choose one label from this list, or \"unknown\":\n{allowed}\n\n"
            "Rules:\n"
            "- Pick the single class matching the drug's primary mechanism.\n"
            "- Judge by the active ingredient, not the brand or the salt form.\n"
            "- If none of the listed classes genuinely fit, return \"unknown\". "
            "A wrong class turns the wrong organ warnings on; \"unknown\" is the "
            "honest answer and leaves the drug where it already was.\n\n"
            'Return JSON: {{"drug_class": "<one label from the list, or unknown>"}}\n\n'
            "Drug name: {drug_name}\n"
            "Ingredients: {ingredients}"
        ),
        validate=_drug_class_is_string,
    ),
    # patient-profiling — organ placement for an effect the substring tables miss
    # (organ_infer.py). REACTION_ORGANS covers the common FAERS/label phrasings,
    # but a rare reaction or an avoided ingredient with no table line would draw
    # nothing and be reported unmapped. This places it on the body, constrained
    # to the organs the figure can actually draw, cached per effect. It fires
    # only for prose-carrying findings (a reaction, an ingredient), never for a
    # code deliberately mapped to "no single organ".
    "organ_impact": AITask(
        name="organ_impact",
        owner="Andrii",
        prompt=(
            "Which organ or organs does this drug effect act on, for a body "
            "diagram?\n\n"
            "Choose only from this list:\n{allowed}\n\n"
            "Rules:\n"
            "- Name the organ(s) where the effect is seen or does its harm.\n"
            "- Use only labels from the list. If none genuinely fit, return an "
            "empty list -- a blank is honest, a wrong organ is a false clinical "
            "claim on the figure.\n"
            "- Most effects are one organ. Return several only when the effect "
            "truly spans them (an anaphylactic reaction: skin and lungs).\n\n"
            'Return JSON: {{"organs": ["<label from the list>", ...]}}\n\n'
            "Effect: {effect}"
        ),
        validate=_organs_is_list,
    ),
    # prediction — Mykhailo
    # compliance (extract) — Andrii
}
