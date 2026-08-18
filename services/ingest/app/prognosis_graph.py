"""PP-3 label extraction as a graph: section -> extract -> validate -> repair -> explain.

The single-shot version asks one model call to read a whole label and emit
structured risk factors, then `_prognosis_is_applicable` throws away everything
it cannot evaluate. That discard is the problem this replaces. A label says

    "...in patients with moderate to severe renal impairment..."

and the model answers `egfr_band at_or_below moderate`, which is not a value in
the vocabulary, so the factor is dropped — the risk survives with one fewer
condition, or vanishes if that was its only one. Nothing records that it
happened. The information was there and we lost it in the last inch.

So validation here **partitions** rather than prunes, and a rejected factor goes
back to the model with the reason and the allowed values, once. What is still
invalid after that is dropped, but it is dropped *on the record*.

Four properties this keeps from the single-shot path, because they are what make
the output admissible at all:

* nothing here touches a patient — public label in, drug-level table out;
* every surviving citation is verbatim in the label;
* every surviving factor is expressible in the collected vocabulary;
* everything lands `awaiting_approval`, so a pharmacist accepts it before it
  can colour anything (docs/prognosis-and-procurement.md §1.3).

The nodes are pure functions of state. LangGraph wires them, and is imported
lazily: `medstock_shared` is imported by seven services and five of them have
neither a Gemini key nor a reason to install a graph library.
"""

from __future__ import annotations

import json
import logging
import operator
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from medstock_shared.ai_tasks import PROGNOSIS_FEATURES, PROGNOSIS_OPS

_log = logging.getLogger(__name__)

# One repair round, not a loop to convergence. A model that cannot express a
# factor in the vocabulary after being shown the vocabulary and its own mistake
# is not going to on the third attempt; it is going to invent something that
# passes. Bounded here rather than by a recursion limit so the reason is legible.
MAX_REPAIR_ROUNDS = 1

# Labels are sectioned before extraction rather than sent whole. Boxed warnings
# and use_in_specific_populations ask different questions of a reader, and one
# 8k-character prompt covering both is where the single-shot version lost the
# quieter one.
SECTION_MARKER = "["


@dataclass(frozen=True)
class Rejection:
    """A factor that did not survive validation, and why.

    Carries enough for two audiences: `reason` is shown to a pharmacist in the
    review queue, `factor` goes back to the model in the repair round.
    """

    factor: dict[str, Any]
    reason: str
    reaction: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"factor": self.factor, "reason": self.reason, "reaction": self.reaction}


@dataclass
class Partition:
    """Validation's answer: what survived, and what did not with reasons."""

    kept: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.rejected


class GraphState(TypedDict, total=False):
    """What flows between nodes.

    `rxcui`, `drug_name` and `sections` are inputs. Everything else accumulates.

    `risks` and `failed_sections` carry `operator.add` reducers because the
    section branches run in PARALLEL and every one of them writes both. Without
    a reducer LangGraph treats concurrent writes to the same key as a conflict;
    with one, six branches append into a single list and the merge is the
    framework's problem rather than ours.
    """

    rxcui: str
    drug_name: str
    spl_id: str
    sections: list[tuple[str, str]]   # (section name, text)
    failed_sections: Annotated[list[str], operator.add]   # never read
    risks: Annotated[list[dict[str, Any]], operator.add]  # extracted, pre-validation
    kept: list[dict[str, Any]]        # validated risks
    rejected: list[dict[str, Any]]    # Rejection.as_dict(), for the reviewer
    repair_rounds: int
    explanation: str        # deterministic, always present
    explanation_note: str   # model prose, supplementary and may be empty


# --- section splitting --------------------------------------------------------


def split_sections(text: str) -> list[tuple[str, str]]:
    """`label_text` joins sections as "[name]\\ntext". Take them apart again.

    Text before any marker is returned under "" rather than dropped: a label
    that does not use the markers must still be extractable, and silently
    returning nothing would look identical to a drug with no conditional risk.
    """
    out: list[tuple[str, str]] = []
    current_name = ""
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(SECTION_MARKER) and stripped.endswith("]"):
            if current:
                out.append((current_name, "\n".join(current).strip()))
                current = []
            current_name = stripped[1:-1]
            continue
        current.append(line)
    if current:
        out.append((current_name, "\n".join(current).strip()))
    return [(name, body) for name, body in out if body]


# --- validation ---------------------------------------------------------------


def validate_factor(factor: Any) -> str | None:
    """`None` if the factor is evaluable, else why it is not.

    The reason is written for a pharmacist reading the review queue, and reused
    verbatim in the repair prompt — one string, so the model is told exactly
    what the reviewer would be told.
    """
    if not isinstance(factor, dict):
        return "not an object"
    feature, op, value = factor.get("feature"), factor.get("op"), factor.get("value")
    if feature not in PROGNOSIS_FEATURES:
        return f"'{feature}' is not a feature this system collects"
    if op not in PROGNOSIS_OPS:
        return f"'{op}' is not a supported operator"
    allowed = PROGNOSIS_FEATURES[feature]
    if allowed is None:
        return None
    values = value if isinstance(value, list) else [value]
    if not values:
        return f"no value given for {feature}"
    unknown = [v for v in values if v not in allowed]
    if unknown:
        return (
            f"{unknown[0]!r} is not a value of {feature} "
            f"(allowed: {', '.join(sorted(allowed))})"
        )
    return None


def validate_risks(risks: Iterable[dict[str, Any]], source_text: str) -> Partition:
    """Partition risks into evaluable and not, with a reason for each rejection.

    A risk whose citation is not verbatim in the label is rejected whole: unlike
    analogue, where a bad quote is blanked and the recommendation stands, a
    prognosis with no reviewable basis is not a prognosis. A risk all of whose
    factors fail is likewise rejected — with no conditions left it would match
    every patient and flag the entire cohort.
    """
    partition = Partition()
    for risk in risks:
        if not isinstance(risk, dict) or not risk.get("reaction"):
            partition.rejected.append(Rejection({}, "no reaction named", ""))
            continue
        reaction = str(risk.get("reaction"))

        quote = risk.get("citation") or ""
        if not quote or quote not in source_text:
            partition.rejected.append(
                Rejection(
                    {},
                    "citation is not a verbatim sentence from the label",
                    reaction,
                )
            )
            continue

        good: list[dict[str, Any]] = []
        for factor in risk.get("risk_factors") or []:
            reason = validate_factor(factor)
            if reason is None:
                kept_factor = {
                    "feature": factor["feature"],
                    "op": factor["op"],
                    "value": factor["value"],
                }
                if factor.get("repaired"):
                    kept_factor["repaired"] = True
                good.append(kept_factor)
            else:
                partition.rejected.append(Rejection(factor, reason, reaction))

        if good:
            partition.kept.append({**risk, "risk_factors": good})
        else:
            partition.rejected.append(
                Rejection({}, "no evaluable risk factor survived", reaction)
            )
    return partition


# --- repair -------------------------------------------------------------------


def repair_payload(rxcui: str, rejected: Iterable[Rejection], source_text: str) -> dict[str, Any]:
    """What the repair round asks. Only the rejects, each with its reason.

    Re-sending the whole label would invite the model to reconsider factors that
    already passed, which turns a repair into a second opinion.
    """
    lines = [
        f"{json.dumps(r.factor, sort_keys=True)} — rejected because {r.reason}"
        for r in rejected
        if r.factor
    ]
    return {
        "rxcui": rxcui,
        "rejected": "\n".join(lines),
        "vocabulary": describe_vocabulary(),
        "source_text": source_text,
    }


def describe_vocabulary() -> str:
    """The allowed features and values, as the prompt shows them.

    Generated from PROGNOSIS_FEATURES rather than written out, so a feature
    added there cannot silently fail to reach the model that has to use it.
    """
    lines = []
    for feature, allowed in PROGNOSIS_FEATURES.items():
        values = ", ".join(sorted(allowed)) if allowed else "free-form"
        lines.append(f"  {feature}: {values}")
    lines.append(f"  operators: {', '.join(sorted(PROGNOSIS_OPS))}")
    return "\n".join(lines)


def merge_repaired(
    kept: list[dict[str, Any]],
    repaired: Iterable[dict[str, Any]],
    source_text: str,
) -> Partition:
    """Fold a repair round's output back in, revalidating it.

    Repaired factors are validated with the same function as the first pass. A
    repair that is still wrong is rejected for good — `MAX_REPAIR_ROUNDS` is
    what stops this becoming a negotiation.
    """
    revalidated = validate_risks(repaired, source_text)
    by_reaction = {r["reaction"].strip().casefold(): r for r in kept}
    for risk in revalidated.kept:
        # Every factor arriving here was re-expressed after being told it was
        # wrong, which is the single most suspect step in this pipeline: a model
        # under correction fits the vocabulary rather than abstaining. "moderate
        # renal impairment" straddles 45-59 and 30-44, and whichever it picks
        # changes which patients get flagged. Marked so a pharmacist can give
        # these the scrutiny the first-pass factors do not need, and so the
        # explanation can list them separately.
        for factor in risk["risk_factors"]:
            factor["repaired"] = True
        key = risk["reaction"].strip().casefold()
        existing = by_reaction.get(key)
        if existing is None:
            by_reaction[key] = risk
            continue
        seen = {
            json.dumps({k: v for k, v in f.items() if k != "repaired"}, sort_keys=True)
            for f in existing["risk_factors"]
        }
        for factor in risk["risk_factors"]:
            marker = json.dumps(
                {k: v for k, v in factor.items() if k != "repaired"}, sort_keys=True
            )
            if marker not in seen:
                seen.add(marker)
                existing["risk_factors"].append(factor)
    return Partition(kept=list(by_reaction.values()), rejected=revalidated.rejected)


# --- explanation --------------------------------------------------------------


def explanation_payload(state: GraphState) -> dict[str, Any]:
    """What the explanation round is given: the outcome, not the label.

    The model is asked to describe an extraction that already happened. It is
    not shown the source again, because an explanation that can reach for the
    label can add a claim the extraction never made, and a reviewer would have
    no way to tell which sentences were which.
    """
    kept = state.get("kept") or []
    rejected = state.get("rejected") or []
    return {
        "rxcui": state.get("rxcui", ""),
        "drug_name": state.get("drug_name", ""),
        "kept": json.dumps(
            [
                {
                    "reaction": r.get("reaction"),
                    "seriousness": r.get("seriousness"),
                    "section": r.get("section"),
                    "risk_factors": r.get("risk_factors"),
                }
                for r in kept
            ],
            sort_keys=True,
        ),
        "dropped": json.dumps(rejected, sort_keys=True),
    }


def fallback_explanation(state: GraphState) -> str:
    """The account a reviewer must read. Generated from the extraction itself,
    so it cannot flatter it or describe a risk that was not found.

    Says less than the model's prose and says it in a fixed shape. That is the
    point: it contains what was dropped and which factors were repaired, so an
    approval is never given against an extraction whose losses are invisible.
    """
    kept = state.get("kept") or []
    rejected = state.get("rejected") or []
    lines = [
        f"{len(kept)} risk profile(s) extracted for {state.get('drug_name') or state.get('rxcui')}."
    ]
    repaired_count = 0
    for risk in kept:
        parts = []
        for f in risk.get("risk_factors") or []:
            text = f"{f['feature']} {f['op']} {f['value']}"
            if f.get("repaired"):
                text += " [re-expressed by the model after a rejected first attempt]"
                repaired_count += 1
            parts.append(text)
        lines.append(
            f"- {risk.get('reaction')} ({risk.get('seriousness')}): {', '.join(parts)}"
        )
    if repaired_count:
        lines.append(
            f"{repaired_count} factor(s) above were re-expressed after the model's first "
            "attempt was rejected. A re-expressed band may be narrower or wider than the "
            "label states — check these against the quote before approving."
        )
    failed = state.get("failed_sections") or []
    if failed:
        lines.append(
            f"{len(failed)} label section(s) could not be read ({', '.join(failed)}). "
            "Any risk stated only there is missing from this extraction."
        )
    if rejected:
        lines.append(f"{len(rejected)} item(s) were dropped and are not represented above:")
        for item in rejected:
            where = f" [{item['reaction']}]" if item.get("reaction") else ""
            lines.append(f"- {item.get('reason')}{where}")
    return "\n".join(lines)


# --- nodes --------------------------------------------------------------------
#
# Each takes state and returns the keys it changed, which is LangGraph's update
# convention and also what makes them testable as plain functions.


def make_nodes(ask: Callable[[str, dict], dict]) -> dict[str, Callable[[GraphState], dict]]:
    """Build the node set against an `ask_ai`-shaped callable.

    Injected rather than imported so the graph can be exercised with a stub. The
    real wiring passes `medstock_shared.ask_ai`, which brings the cache, the 429
    retries and the per-task validators with it.
    """

    def extract_section_node(state: dict) -> dict:
        """ONE section, and the unit of parallelism.

        The sections of a label are independent questions -- a boxed warning
        says nothing about how use_in_specific_populations should be read -- so
        there is no reason to wait for one before asking the next. Six sections
        ran as six sequential calls purely because a for-loop was the obvious
        way to write it; as branches they run at once and the label costs about
        as long as its slowest section.

        Failure stays per-branch. A section that fails contributes no risks and
        records its name; a reviewer looking at three profiles cannot otherwise
        tell whether the fourth section was clean or simply never read, which is
        the invisible loss this module exists to stop.
        """
        name, body = state["section"]
        try:
            result = ask(
                "prognosis_section",
                {
                    "rxcui": state.get("rxcui", ""),
                    "drug_name": state.get("drug_name", ""),
                    "section": name,
                    "source_text": body,
                },
            )
        except Exception as exc:  # noqa: BLE001 — one section must not end the label
            _log.warning("prognosis: section %s failed: %s", name, exc)
            return {"risks": [], "failed_sections": [name]}

        risks = [
            {**risk, "section": risk.get("section") or name}
            for risk in result.get("risks") or []
            if isinstance(risk, dict)
        ]
        return {"risks": risks, "failed_sections": []}

    def validate_node(state: GraphState) -> dict:
        partition = validate_risks(state.get("risks") or [], _source_of(state))
        return {
            "kept": partition.kept,
            "rejected": [r.as_dict() for r in partition.rejected],
        }

    def repair_node(state: GraphState) -> dict:
        """Ask again for the factors that failed, with the reason and the
        vocabulary. Failure here leaves the rejections standing."""
        rejected = [
            Rejection(item.get("factor") or {}, item.get("reason") or "", item.get("reaction") or "")
            for item in state.get("rejected") or []
        ]
        repairable = [r for r in rejected if r.factor]
        rounds = int(state.get("repair_rounds") or 0) + 1
        if not repairable:
            return {"repair_rounds": rounds}
        try:
            result = ask(
                "prognosis_repair",
                repair_payload(state.get("rxcui", ""), repairable, _source_of(state)),
            )
        except Exception:  # noqa: BLE001 — a failed repair keeps the first pass
            return {"repair_rounds": rounds}

        merged = merge_repaired(
            state.get("kept") or [], result.get("risks") or [], _source_of(state)
        )
        # Rejections that were not repairable in the first place still stand.
        unrepairable = [r.as_dict() for r in rejected if not r.factor]
        return {
            "kept": merged.kept,
            "rejected": unrepairable + [r.as_dict() for r in merged.rejected],
            "repair_rounds": rounds,
        }

    def explain_node(state: GraphState) -> dict:
        """The reviewer's account of the extraction — the last stage.

        The deterministic account is always the `explanation`, never a fallback.
        Fluent prose next to an approval button raises approval rates, which is
        the opposite of what a gate is for: a generated summary makes an
        extraction *feel* vetted, and the reviewer is the only thing standing
        between a model's reading of a label and a patient. So the account they
        must read is the one that is true by construction, and the model's prose
        is an addition to it, labelled, and absent when the call fails.
        """
        determined = fallback_explanation(state)
        try:
            result = ask("prognosis_explain", explanation_payload(state))
        except Exception:  # noqa: BLE001 — the gate must not need a live model
            return {"explanation": determined, "explanation_note": ""}
        return {
            "explanation": determined,
            "explanation_note": str(result.get("explanation") or "").strip(),
        }

    return {
        "extract_section": extract_section_node,
        "validate": validate_node,
        "repair": repair_node,
        "explain": explain_node,
    }


def _source_of(state: GraphState) -> str:
    """The whole label, reassembled. Citations are checked against every
    section, not just the one a risk came from, because the model is told to
    quote the label and a boxed warning is quoted in both places."""
    return "\n\n".join(body for _name, body in state.get("sections") or [])


def fan_out_sections(state: GraphState) -> list:
    """One `Send` per section: the fan-out itself.

    A label with no sections sends nothing, and the graph proceeds straight to
    validate with an empty extraction -- which is the correct reading of a drug
    whose label carries no conditional risk, and not an error.
    """
    from langgraph.types import Send

    return [
        Send(
            "extract_section",
            {
                "rxcui": state.get("rxcui", ""),
                "drug_name": state.get("drug_name", ""),
                "section": (name, body),
            },
        )
        for name, body in state.get("sections") or []
    ]


def needs_repair(state: GraphState) -> str:
    """Conditional edge: repair once if anything repairable was rejected."""
    if int(state.get("repair_rounds") or 0) >= MAX_REPAIR_ROUNDS:
        return "explain"
    if any(item.get("factor") for item in state.get("rejected") or []):
        return "repair"
    return "explain"


# --- the graph ----------------------------------------------------------------


def build_graph(ask: Callable[[str, dict], dict] | None = None):
    """Compile the extraction graph.

    LangGraph is imported here, not at module scope. Five of the seven services
    import `medstock_shared` and have no reason to carry a graph library; an
    import at the top would make this module's presence a dependency for all of
    them.
    """
    from langgraph.graph import END, START, StateGraph

    if ask is None:
        from . import ask_ai as _ask_ai

        ask = _ask_ai

    nodes = make_nodes(ask)
    graph = StateGraph(GraphState)
    for name, fn in nodes.items():
        graph.add_node(name, fn)

    # Fan out: one branch per section, all in flight at once. `Send` carries the
    # section into the branch rather than the branch reading it out of shared
    # state, which is what lets the same node run many times concurrently
    # without the copies treading on each other.
    graph.add_conditional_edges(START, fan_out_sections, ["extract_section"])
    # Fan in. Every branch writes `risks` and `failed_sections`, whose reducers
    # concatenate; validate does not run until all of them have landed.
    graph.add_edge("extract_section", "validate")
    # validate decides whether a repair round is worth a call.
    graph.add_conditional_edges("validate", needs_repair, {"repair": "repair", "explain": "explain"})
    # A repair is revalidated inside repair_node, so it goes straight on: the
    # round counter, not a second validate, is what bounds this.
    graph.add_edge("repair", "explain")
    graph.add_edge("explain", END)
    return graph.compile()
