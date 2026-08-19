"""CronJob entrypoint: FDA label prose -> conditional risk profiles (PP-3).

The one place in this system where a model is doing work nothing else can. The
risk factors are already in the label, as English:

    "Risk factors for metformin-associated lactic acidosis include renal
     impairment, concomitant use of certain drugs, age 65 years old or greater,
     ... and hepatic impairment."

Four of those are fields in the patient feature vector. There is no parser for
that sentence, so a model reads it and emits structured factors; everything
afterwards is arithmetic.

**Nothing here touches a patient.** Input is a public label, output is a
drug-level table. The model never sees clinical data, which is why this can run
at all without the BAA question (docs/phi-readiness.md §2).

**Nothing here is trusted.** `_prognosis_is_applicable` in `ai_tasks.py` drops
factors outside the collected vocabulary and risks whose citation is not
verbatim in the label. What survives lands `awaiting_approval` — a pharmacist
accepts each profile before it can colour anything.

  python -m app.prognosis --rxcui 861007 314076        # named drugs
  python -m app.prognosis --formulary --limit 20       # what a hospital stocks
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx
from medstock_shared import AIError, ask_ai
from medstock_shared.db import SessionLocal
from medstock_shared.models import DrugRiskProfile, FormularyItem
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ._source import fetch_json

LABEL_URL = "https://api.fda.gov/drug/label.json"

# Sections that carry conditional risk. `adverse_reactions` is deliberately
# excluded: it is a frequency table of everything observed, mostly without a
# patient qualifier, and it is enormous.
SECTIONS = (
    "boxed_warning",
    "warnings_and_cautions",
    "warnings",
    "use_in_specific_populations",
    "geriatric_use",
    "contraindications",
)

# Labels run to hundreds of KB. Cost and prompt limits both say to send the
# sections that matter, truncated, not the document.
MAX_CHARS = 8_000

# Worst first, so a merge keeps the gravest assessment of a shared reaction.
_SERIOUSNESS_RANK = {"fatal": 0, "serious": 1, "moderate": 2, "mild": 3}


def label_text(rxcui: str) -> tuple[str, str] | None:
    """Concatenated risk sections for one RxCUI, plus the SPL id it came from.

    Returns `None` when openFDA has no label — a real outcome for many RxCUIs,
    not an error.
    """
    try:
        data = fetch_json(LABEL_URL, params={"search": f'openfda.rxcui:"{rxcui}"', "limit": 1})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    results = data.get("results") or []
    if not results:
        return None

    record = results[0]
    parts: list[str] = []
    for section in SECTIONS:
        value = record.get(section)
        if not value:
            continue
        text = " ".join(value) if isinstance(value, list) else str(value)
        parts.append(f"[{section}]\n{text}")
    if not parts:
        return None
    return "\n\n".join(parts)[:MAX_CHARS], str(record.get("id") or record.get("set_id") or "")


def drug_name(rxcui: str, text: str) -> str:
    """Best-effort label for the prompt. The model is told the RxCUI too, so a
    missing name degrades the prompt rather than breaking it."""
    return f"RxCUI {rxcui}"


def _ask_with_backoff(payload: dict, attempts: int = 4) -> dict:
    """Retry a busy model.

    `ask_ai` deliberately retries 429 only: on the request path a 503 means
    degrade now rather than make a pharmacist wait. Offline that is the wrong
    trade — nobody is waiting, and "high demand" clears in seconds. Measured:
    4 of 10 drugs failed 503 on the first pass and succeeded on a retry.

    Kept here rather than widened in `ai.py`, so `analogue`'s latency is
    unaffected.
    """
    delay = 5.0
    for attempt in range(1, attempts + 1):
        try:
            return ask_ai("prognosis", payload)
        except AIError as exc:
            transient = "503" in str(exc) or "UNAVAILABLE" in str(exc) or "429" in str(exc)
            if not transient or attempt == attempts:
                raise
            print(f"      busy, retrying in {delay:.0f}s ({attempt}/{attempts - 1})")
            time.sleep(delay)
            delay *= 2
    raise AIError("unreachable")


# Most serious placement first. FDA puts in a boxed warning what it most wants
# read, so when one reaction is described in several sections that is the quote
# worth keeping.
_SECTION_RANK = {name: i for i, name in enumerate(SECTIONS)}


def merge_by_reaction(rows: list[dict]) -> list[dict]:
    """One row per reaction, with every section's risk factors unioned.

    A label routinely says the same thing more than once: metformin's lactic
    acidosis is stated in the boxed warning *and* twice in warnings and
    cautions, and the extraction faithfully returns all three. Both obvious
    ways of handling that are wrong.

    Writing three rows makes `prognosis_findings` emit three findings for one
    reaction and score it three times over, which inflates the very number a
    pharmacist is meant to weigh. Letting the natural key collapse them
    silently discards two — and which one survives is upsert order, so the
    boxed warning, the most serious statement of the three, is as likely to be
    thrown away as kept. That is what actually happened the first time this ran
    against a real label.

    So: union the factors, keep the quote and section from the most
    authoritative placement, and take the worst seriousness anyone assigned.
    The dropped quotes are a real loss, but they restate a reaction already
    represented, and gate 2 still holds on the quote that remains.
    """
    merged: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["rxcui"], row["reaction"].strip().casefold())
        current = merged.get(key)
        if current is None:
            merged[key] = {**row, "risk_factors": list(row["risk_factors"])}
            continue

        seen = {json.dumps(f, sort_keys=True) for f in current["risk_factors"]}
        for factor in row["risk_factors"]:
            marker = json.dumps(factor, sort_keys=True)
            if marker not in seen:
                seen.add(marker)
                current["risk_factors"].append(factor)

        if _SECTION_RANK.get(row["section"], 99) < _SECTION_RANK.get(current["section"], 99):
            current["citation"] = row["citation"]
            current["section"] = row["section"]
        if _SERIOUSNESS_RANK.get(row["seriousness"], 9) < _SERIOUSNESS_RANK.get(
            current["seriousness"], 9
        ):
            current["seriousness"] = row["seriousness"]
    return list(merged.values())


def extract_with_graph(rxcui: str) -> list[dict]:
    """The graph path: a call per section, one repair round, then an account of
    what happened (`prognosis_graph`).

    Kept behind `--graph` rather than made the default, because it costs a call
    per section plus up to two more — six to eight against one — and the case
    for that spend is the factors the single-shot path drops. How often that
    actually happens is what `scripts/prognosis_drop_report.py` measures. Switch
    the default when the number justifies it, not before.
    """
    from .prognosis_graph import build_graph, split_sections

    found = label_text(rxcui)
    if found is None:
        return []
    text, spl_id = found

    final = build_graph().invoke(
        {
            "rxcui": str(rxcui),
            "drug_name": drug_name(rxcui, text),
            "spl_id": spl_id,
            "sections": split_sections(text),
        }
    )

    explanation = final.get("explanation") or ""
    note = final.get("explanation_note") or ""
    if note:
        # Labelled, and after the deterministic account rather than instead of
        # it. The reviewer's own summary is the one generated from the data.
        header = "--- model summary (generated) ---"
        explanation = f"{explanation}\n\n{header}\n{note}"

    return merge_by_reaction(
        [
            {
                "rxcui": str(rxcui),
                "reaction": str(risk.get("reaction"))[:200],
                "seriousness": str(risk.get("seriousness") or "moderate")[:20],
                "risk_factors": risk.get("risk_factors") or [],
                "citation": str(risk.get("citation"))[:2000],
                "section": str(risk.get("section") or "")[:60],
                "spl_id": spl_id,
                "status": "awaiting_approval",
                "extraction_note": explanation[:8000],
            }
            for risk in final.get("kept") or []
        ]
    )


def extract(rxcui: str) -> list[dict]:
    """Risk profiles for one drug. `[]` means no label, or nothing conditional
    in it — both are ordinary."""
    found = label_text(rxcui)
    if found is None:
        return []
    text, spl_id = found

    result = _ask_with_backoff(
        {"rxcui": rxcui, "drug_name": drug_name(rxcui, text), "source_text": text},
    )
    return merge_by_reaction(
        [
            {
                "rxcui": str(rxcui),
                "reaction": str(risk.get("reaction"))[:200],
                "seriousness": str(risk.get("seriousness") or "moderate")[:20],
                "risk_factors": risk.get("risk_factors") or [],
                "citation": str(risk.get("citation"))[:2000],
                "section": str(risk.get("section") or "")[:60],
                "spl_id": spl_id,
                "status": "awaiting_approval",
            }
            for risk in result.get("risks") or []
        ]
    )


# What re-extraction resets, over and above the extracted values themselves.
#
# The review columns go with `status`. Resetting the status but leaving the
# reviewer behind would put a pharmacist's name on a profile they have never
# seen — the row would read `awaiting_approval`, reviewed by someone, which is
# not a state the gate has.
#
# `extracted_at` is refreshed here rather than by an `onupdate` on the column,
# because an onupdate fires on *any* update, including a pharmacist's ruling —
# which would restamp the extraction date every time someone approved a profile
# and quietly break the versioning gate 4 depends on.
_RESET_ON_REEXTRACTION = {
    "reviewed_by": None,
    "reviewed_at": None,
    "review_note": "",
    "extracted_at": func.now(),
}


def write(rows: list[dict]) -> int:
    """Upsert on (rxcui, reaction). Re-extracting the same drug refreshes the
    factors and the quote, and **resets approval** — a profile a pharmacist
    accepted is not silently replaced by a new one they have not seen.
    """
    if not rows:
        return 0
    with SessionLocal() as session:
        for row in rows:
            updates = {k: v for k, v in row.items() if k not in ("rxcui", "reaction")}
            session.execute(
                insert(DrugRiskProfile)
                .values(**row)
                .on_conflict_do_update(
                    index_elements=["rxcui", "reaction"],
                    set_={**updates, **_RESET_ON_REEXTRACTION},
                )
            )
        session.commit()
    return len(rows)


def formulary_rxcuis(limit: int) -> list[str]:
    with SessionLocal() as session:
        return [
            str(r) for r in session.scalars(
                select(FormularyItem.rxcui).distinct().limit(limit)
            ).all()
        ]


def run(rxcuis: list[str], use_graph: bool = False) -> tuple[int, int]:
    """Returns (drugs with at least one profile, total profiles written)."""
    extractor = extract_with_graph if use_graph else extract
    drugs = profiles = 0
    for rxcui in rxcuis:
        try:
            rows = extractor(rxcui)
        except Exception as exc:  # noqa: BLE001 — one bad label must not end the run
            print(f"  {rxcui}: FAILED {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
            continue
        if rows:
            drugs += 1
            profiles += write(rows)
            print(f"  {rxcui}: {len(rows)} risk profile(s)")
        else:
            print(f"  {rxcui}: nothing conditional in the label")
    return drugs, profiles


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract PP-3 risk profiles from FDA labels")
    parser.add_argument("--rxcui", nargs="*", default=[])
    parser.add_argument("--formulary", action="store_true", help="use the stocked formulary")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--graph",
        action="store_true",
        help="use the section/repair/explain graph instead of one call per label",
    )
    args = parser.parse_args()

    rxcuis = list(args.rxcui)
    if args.formulary:
        rxcuis += formulary_rxcuis(args.limit)
    if not rxcuis:
        print("nothing to do: pass --rxcui or --formulary", file=sys.stderr)
        return 2

    drugs, profiles = run(rxcuis[: args.limit], use_graph=args.graph)
    print(f"\n{profiles} profile(s) across {drugs}/{len(rxcuis[:args.limit])} drug(s), "
          f"all awaiting_approval")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
