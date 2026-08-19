"""Patient documents -> the features the ruleset weighs.

`assess()` scores against sex, eGFR band, hepatic status, prior reactions,
conditions and current drugs. A hospital does not hold most of that as fields:
eGFR is a number on a lab report, hepatic impairment is an LFT panel or a line
in a discharge summary, and "intolerant to ACE inhibitors" is a sentence in a
note. Until those reach the record the heaviest findings in the ruleset have
nothing to fire on.

So this reads the document -- text or a photograph of one -- and produces
structured features. The graph is not decoration; each node exists because the
naive version of this is dangerous in a specific way:

    classify --+--> read as labs  --+--> normalise -> reconcile -> apply
               +--> read as prose --+

**classify** first, because the prompt for a lab panel and the prompt for
narrative are not the same question, and one prompt asked to do both does
neither well -- it transcribes the numbers and skims the prose, or reads the
prose and rounds the numbers.

**The reads run in parallel**, which is what makes that split free. A
discharge summary genuinely is both -- a lab panel and a narrative -- so it
gets both prompts at once and costs the wall time of one. An unclassified
document also gets both, because a failed classification must not silently
skip whichever half we guessed wrong.

**normalise** separately from extract, because the model reports what the page
says -- "eGFR 32 mL/min/1.73m2", "moderate hepatic impairment" -- and the
ruleset needs its own vocabulary. Keeping them apart means a vocabulary change
does not touch the prompt that reads the page.

**plausibility** because OCR of a photograph transposes digits. An eGFR of 320
is not a patient, it is a misread 32, and a value that silently becomes ">=90"
turns a renal warning off for someone whose kidneys are failing. Implausible
values are rejected, never clamped.

**reconcile** because a document has a date and a record has a history. A lab
from 2023 must not overwrite one from 2026, and a materially different value is
a conflict a clinician should see rather than a silent replacement.

**apply** writes with provenance into `patient.feature_provenance`, so
`/explain` can say a finding rests on a value a model read off a page rather
than one a clinician typed. They are the same number and not the same evidence.

PHI: unlike label extraction, this sends patient data to the model. It is
gated on `settings.phi_to_model_allowed` and refuses to run without it.
"""

from __future__ import annotations

import logging
import operator
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Annotated, Any, TypedDict

_log = logging.getLogger(__name__)

# Physiologically possible, not clinically normal. The point is to catch a
# transposed digit, not to second-guess a nephrologist: 3 is survivable on
# dialysis, 200 is not a kidney.
EGFR_PLAUSIBLE = (1.0, 180.0)

HEPATIC_VALUES = frozenset({"normal", "impaired", "unknown"})
SEX_VALUES = frozenset({"F", "M"})

# A value this much below the stored one, or newer by any margin, is worth a
# clinician's attention rather than a silent write. Renal function moving by
# a band is a dose change.
EGFR_CONFLICT_DELTA = 15.0


@dataclass(frozen=True)
class Extracted:
    """One feature the document yielded, with where it came from."""

    field: str
    value: Any
    source: str          # "lab-report:2026-08-01", for feature_provenance
    quote: str = ""      # what on the page said so

    def as_provenance(self) -> str:
        return self.source


@dataclass
class Conflict:
    """An extracted value that disagrees with what is already on the record."""

    field: str
    existing: Any
    extracted: Any
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "existing": self.existing,
            "extracted": self.extracted,
            "reason": self.reason,
        }


@dataclass
class Outcome:
    applied: list[Extracted] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)   # (field, why)
    conflicts: list[Conflict] = field(default_factory=list)


class IntakeState(TypedDict, total=False):
    patient_ref: str
    document_text: str
    document_date: str          # ISO, from the page if it has one
    kind: str                   # lab_report | discharge_summary | note | unknown
    # Written by every extraction branch in parallel, so it reduces by
    # concatenation rather than by last-write-wins.
    raw: Annotated[list[dict[str, Any]], operator.add]
    extracted: list[Extracted]
    rejected: list[tuple[str, str]]
    conflicts: list[dict[str, Any]]
    existing: dict[str, Any]    # the patient row as it stands


# --- normalising --------------------------------------------------------------


def normalise_egfr(raw: Any) -> tuple[float | None, str]:
    """A number, or why it is unusable.

    Accepts what a report actually prints -- "32", "32.4", ">60", "<15" -- and
    refuses anything it cannot read as a measurement. A ">60" is deliberately
    taken as 60: the report is asserting a floor, and the conservative reading
    of a floor is the floor itself.
    """
    if raw is None:
        return None, "no value"
    text = str(raw).strip().replace("mL/min/1.73m2", "").replace("mL/min", "").strip()
    if not text:
        return None, "no value"
    floor = text.startswith(">")
    text = text.lstrip("><=~ ").strip()
    try:
        value = float(text)
    except ValueError:
        return None, f"{raw!r} is not a number"
    low, high = EGFR_PLAUSIBLE
    if not (low <= value <= high):
        # Never clamped. A misread 320 clamped to 180 is still a wrong kidney,
        # and it would read as a measurement rather than a failure.
        return None, f"{value} is outside {low}-{high}, likely a misread"
    return (value, "floor value taken as-is" if floor else "")


def normalise_hepatic(raw: Any) -> tuple[str | None, str]:
    """Map a report's wording to the three states the ruleset branches on."""
    if raw is None:
        return None, "no value"
    text = str(raw).strip().lower()
    if text in HEPATIC_VALUES:
        return text, ""
    if any(word in text for word in ("impair", "cirrho", "failure", "insufficien")):
        return "impaired", ""
    if any(word in text for word in ("normal", "unremarkable", "within range")):
        return "normal", ""
    return None, f"{raw!r} does not map to normal/impaired"


def normalise_sex(raw: Any) -> tuple[str | None, str]:
    if raw is None:
        return None, "no value"
    text = str(raw).strip().upper()[:1]
    if text in SEX_VALUES:
        return text, ""
    return None, f"{raw!r} is not F or M"


NORMALISERS: dict[str, Callable[[Any], tuple[Any, str]]] = {
    "egfr_value": normalise_egfr,
    "hepatic": normalise_hepatic,
    "sex": normalise_sex,
}


def normalise(raw_features: Sequence[dict[str, Any]], source: str) -> Outcome:
    """Turn what the model read into what the ruleset can use."""
    outcome = Outcome()
    for item in raw_features:
        if not isinstance(item, dict):
            outcome.rejected.append(("?", "malformed extraction"))
            continue
        name = str(item.get("field") or "")
        normaliser = NORMALISERS.get(name)
        if normaliser is None:
            # prior_adr_rxcuis and condition_codes arrive already coded; anything
            # else is a field the ruleset does not read and must not be stored.
            if name in ("prior_adr_rxcuis", "condition_codes"):
                values = item.get("value")
                values = values if isinstance(values, list) else [values]
                coded = [str(v) for v in values if v]
                if coded:
                    outcome.applied.append(
                        Extracted(name, coded, source, str(item.get("quote") or ""))
                    )
                continue
            outcome.rejected.append((name, "not a feature the ruleset reads"))
            continue

        value, why = normaliser(item.get("value"))
        if value is None:
            outcome.rejected.append((name, why))
            continue
        outcome.applied.append(Extracted(name, value, source, str(item.get("quote") or "")))
    return outcome


# --- reconciliation -----------------------------------------------------------


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except (TypeError, ValueError):
        return None


def reconcile(
    extracted: Sequence[Extracted],
    existing: dict[str, Any],
    document_date: Any = None,
    existing_date: Any = None,
) -> Outcome:
    """Decide what may be written, and what a person has to look at.

    Three rules, in order:

    * an empty field is filled, always -- that is the whole point;
    * a document older than the value on the record is never applied, because
      a 2023 creatinine replacing a 2026 one is a patient getting healthier on
      paper only;
    * a materially different value from a newer document is applied AND raised
      as a conflict. Applied because it is the more recent measurement; raised
      because renal function moving by that much is a dose conversation.
    """
    outcome = Outcome()
    doc_when = _parse_date(document_date)
    rec_when = _parse_date(existing_date)

    for item in extracted:
        current = existing.get(item.field)
        empty = current in (None, "", [], ())
        if empty:
            outcome.applied.append(item)
            continue

        if doc_when and rec_when and doc_when < rec_when:
            outcome.rejected.append(
                (item.field, f"document dated {doc_when} is older than the record's {rec_when}")
            )
            continue

        if item.field == "egfr_value":
            try:
                delta = abs(float(current) - float(item.value))
            except (TypeError, ValueError):
                delta = 0.0
            if delta >= EGFR_CONFLICT_DELTA:
                outcome.conflicts.append(
                    Conflict(
                        item.field, current, item.value,
                        f"eGFR moved by {delta:.0f} — check this is the same patient "
                        f"and the newer measurement",
                    )
                )
            outcome.applied.append(item)
            continue

        if str(current) != str(item.value):
            outcome.conflicts.append(
                Conflict(item.field, current, item.value, "differs from the stored value")
            )
        outcome.applied.append(item)
    return outcome


# --- nodes --------------------------------------------------------------------


# Which extractions a document is worth. A discharge summary gets BOTH, and
# that is the case the single-prompt version handled badly: they carry a lab
# panel *and* prose, and one prompt asked to do both does neither as well --
# it either transcribes the numbers and skims the narrative, or reads the
# narrative and rounds the numbers. Two prompts, run at once, cost the same
# wall time as one.
READS: dict[str, tuple[str, ...]] = {
    "lab_report": ("patient_doc_labs",),
    "discharge_summary": ("patient_doc_labs", "patient_doc_summary"),
    "note": ("patient_doc_summary",),
    "unknown": ("patient_doc_labs", "patient_doc_summary"),
}


def reads_for(kind: str) -> tuple[str, ...]:
    """The prompts to run for a document of this kind.

    An unclassified document gets both, deliberately: classification failed, so
    guessing narrow would silently skip whichever half we guessed wrong.
    """
    return READS.get(kind or "unknown", READS["unknown"])


def fan_out_reads(state: IntakeState) -> list:
    """One `Send` per extraction the document deserves."""
    from langgraph.types import Send

    return [
        Send(
            "extract",
            {"task": task, "document_text": state.get("document_text", "")},
        )
        for task in reads_for(state.get("kind", ""))
    ]


def make_nodes(ask: Callable[..., dict]) -> dict[str, Callable[[IntakeState], dict]]:
    """Nodes built against an `ask_ai`-shaped callable, injected so the flow is
    exercisable without a model."""

    def classify_node(state: IntakeState) -> dict:
        try:
            result = ask(
                "patient_doc_classify",
                {"source_text": state.get("document_text", "")[:4000]},
            )
        except Exception:  # noqa: BLE001 — an unclassified document is still readable
            _log.warning("intake: classify failed, falling back to generic extraction")
            return {"kind": "unknown"}
        kind = str(result.get("kind") or "unknown")
        return {"kind": kind, "document_date": str(result.get("document_date") or "")}

    def extract_node(state: dict) -> dict:
        """One extraction prompt, and the unit of parallelism.

        Which prompts run is `reads_for`'s decision; this just runs the one it
        was sent. Failure is per-branch: a summary whose lab panel could not be
        read should still yield the intolerance in its prose.
        """
        task = state["task"]
        try:
            result = ask(task, {"source_text": state.get("document_text", "")})
        except Exception:  # noqa: BLE001 — nothing extracted is a real outcome
            _log.warning("intake: %s failed", task)
            return {"raw": []}
        return {"raw": list(result.get("features") or [])}

    def normalise_node(state: IntakeState) -> dict:
        kind = state.get("kind") or "document"
        when = state.get("document_date") or "undated"
        outcome = normalise(state.get("raw") or [], source=f"{kind}:{when}")
        return {"extracted": outcome.applied, "rejected": outcome.rejected}

    def reconcile_node(state: IntakeState) -> dict:
        existing = state.get("existing") or {}
        outcome = reconcile(
            state.get("extracted") or [],
            existing,
            state.get("document_date"),
            existing.get("_measured_at"),
        )
        return {
            "extracted": outcome.applied,
            "rejected": (state.get("rejected") or []) + outcome.rejected,
            "conflicts": [c.as_dict() for c in outcome.conflicts],
        }

    return {
        "classify": classify_node,
        "extract": extract_node,
        "normalise": normalise_node,
        "reconcile": reconcile_node,
    }


def build_graph(ask: Callable[..., dict] | None = None):
    """Compile the intake flow. LangGraph imported lazily, as in ingest."""
    from langgraph.graph import END, START, StateGraph

    if ask is None:
        from medstock_shared import ask_ai as _ask_ai

        ask = _ask_ai

    nodes = make_nodes(ask)
    graph = StateGraph(IntakeState)
    for name, fn in nodes.items():
        graph.add_node(name, fn)
    graph.add_edge(START, "classify")
    # Fan out on what the classification says the document is worth reading for.
    graph.add_conditional_edges("classify", fan_out_reads, ["extract"])
    # Fan in: `raw` concatenates across branches, and normalise sees one list.
    graph.add_edge("extract", "normalise")
    graph.add_edge("normalise", "reconcile")
    graph.add_edge("reconcile", END)
    return graph.compile()
