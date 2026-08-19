"""Tier 1 feed: FAERS disproportionality into `adr_signal`.

docs/patient-profiling-usecases.md §3 Tier 1 and §4. Computes the two standard
spontaneous-reporting measures per drug × reaction and stores them for stage 7 to
look up. Nothing is computed on a request.

The 2×2 table, for one drug D and one reaction R:

                    reaction R    not R
    drug D              a           b
    not D               c           d

    PRR = [a / (a+b)] / [c / (c+d)]
    ROR = (a·d) / (b·c)

openFDA gives every cell with three calls in total plus one per drug:

* `count=patient.reaction.reactionmeddrapt.exact` unfiltered  -> a+c per reaction
* `meta.results.total` on an unfiltered search                -> a+b+c+d
  (a count query carries no `total`, so this is a separate plain search)
* the same `count` filtered to the drug                       -> a, and Σa = a+b

**Budget.** openFDA allows 1 000 requests a day *per IP, shared across every
feed* (docs/services.md §7). This is one call per drug plus two fixed, so
`--limit` exists and defaults low. Running it over a whole formulary is a
deliberate act, not a default.

A caveat that belongs in the code and not only in a doc: these are **reporting
ratios, not risks**. FAERS has no denominator, is subject to notoriety bias, and
is confounded by indication. The finding text stage 7 emits says so on every row.

Run:  uv run python -m app.faers --rxcui 861007 29046
      uv run python -m app.faers --formulary --limit 25
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

import httpx
from medstock_shared.db import SessionLocal, iter_hospitals
from medstock_shared.models import AdrSignal, FormularyItem
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ._source import fetch_json

EVENT = "https://api.fda.gov/drug/event.json"
REACTION_FIELD = "patient.reaction.reactionmeddrapt.exact"

# **100, not 1 000.** Asking a count query for `limit=1000` returns
# `403 API_KEY_MISSING` — the higher ceiling is a keyed feature, and every other
# feed here is keyless by design (docs/services.md §7). Keyless callers get the
# top 100 terms.
#
# That is a real coverage limit, not a formality: the reaction distribution is
# long-tailed, so a drug-specific reaction outside the overall top 100 has no
# baseline here and is skipped rather than given a guessed one. Registering an
# openFDA key would raise this to 1 000 and materially widen Tier 1; it is an
# ops decision, not a code one, so the keyless number is what ships.
COUNT_LIMIT = 100

# Below this, a ratio is arithmetic on noise. Kept here as well as in the
# matcher so the table does not fill with rows the matcher will always discard.
MIN_REPORTS = 3


def _fetch(params: dict) -> dict:
    """openFDA answers **404 when a search matches nothing**, which for a drug
    with no adverse-event reports is an ordinary result and not a failure. The
    retry helper already treats 404 as "asking again will not help"; this turns
    it into an empty answer so one unreported drug does not read as an error."""
    try:
        return fetch_json(EVENT, params)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return {}
        raise


def _counts(params: dict) -> dict[str, int]:
    body = _fetch(params)
    return {
        str(row.get("term", "")).strip(): int(row.get("count") or 0)
        for row in (body.get("results") or [])
        if row.get("term")
    }


def _total(search: str | None = None) -> int:
    """Total reports matching a search — the `a+b` and `a+b+c+d` cells."""
    params = {"limit": 1}
    if search:
        params["search"] = search
    body = _fetch(params)
    return int((body.get("meta") or {}).get("results", {}).get("total") or 0)


def background() -> tuple[dict[str, int], int]:
    """(reports per reaction across all drugs, total reports)."""
    return _counts({"count": REACTION_FIELD, "limit": COUNT_LIMIT}), _total()


def signals_for(rxcui: str, reaction_totals: dict[str, int], grand_total: int) -> list[dict]:
    search = f'patient.drug.openfda.rxcui:"{rxcui}"'
    drug_counts = _counts({"search": search, "count": REACTION_FIELD, "limit": COUNT_LIMIT})
    if not drug_counts:
        return []
    n_drug = sum(drug_counts.values())

    rows: list[dict] = []
    for reaction, a in drug_counts.items():
        if a < MIN_REPORTS:
            continue
        a_plus_c = reaction_totals.get(reaction)
        if not a_plus_c:
            continue  # outside the keyless top 100 — no baseline, so no claim
        b = n_drug - a
        c = a_plus_c - a
        d = grand_total - n_drug - c
        # Any empty cell makes both measures undefined. Skipping is right:
        # substituting a continuity correction here would invent precision the
        # data does not have.
        if b <= 0 or c <= 0 or d <= 0:
            continue
        prr = (a / (a + b)) / (c / (c + d))
        ror = (a * d) / (b * c)
        rows.append(
            {
                "rxcui": str(rxcui),
                "reaction": reaction,
                "prr": round(prr, 3),
                "ror": round(ror, 3),
                "n_reports": a,
                "n_drug_reports": n_drug,
                # Explicit: the upsert below means onupdate never fires, and a
                # ratio that silently keeps its first-ever timestamp cannot be
                # told apart from a feed that stopped running.
                "computed_at": datetime.now(tz=UTC),
            }
        )
    return rows


def formulary_rxcuis(limit: int) -> list[str]:
    if limit <= 0:
        return []
    with SessionLocal() as session:
        all_rxcuis: set[str] = set()
        for _ in iter_hospitals(session):
            for r in session.scalars(
                select(FormularyItem.rxcui).where(FormularyItem.rxcui.is_not(None))
            ):
                all_rxcuis.add(str(r))
                if len(all_rxcuis) >= limit:
                    break
            if len(all_rxcuis) >= limit:
                break
        return list(all_rxcuis)


def write(rows: list[dict]) -> int:
    if not rows:
        return 0
    with SessionLocal() as session:
        for row in rows:
            session.execute(
                insert(AdrSignal)
                .values(**row)
                .on_conflict_do_update(
                    index_elements=["rxcui", "reaction"],
                    set_={k: v for k, v in row.items() if k not in ("rxcui", "reaction")},
                )
            )
        session.commit()
    return len(rows)


def run(rxcuis: list[str]) -> tuple[int, int]:
    reaction_totals, grand_total = background()
    print(f"  baseline: {len(reaction_totals)} reactions over {grand_total:,} reports")
    drugs = written = 0
    for rxcui in rxcuis:
        try:
            rows = signals_for(rxcui, reaction_totals, grand_total)
        except Exception as exc:  # noqa: BLE001 — one bad drug must not end the run
            print(f"  {rxcui}: FAILED {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
            continue
        if rows:
            drugs += 1
            written += write(rows)
            top = max(rows, key=lambda r: r["prr"])
            print(f"  {rxcui}: {len(rows)} signal(s), highest PRR {top['prr']} ({top['reaction']})")
        else:
            print(f"  {rxcui}: no reports, or no reaction clears the floor")
    return drugs, written


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute FAERS PRR/ROR into adr_signal")
    parser.add_argument("--rxcui", nargs="*", default=[])
    parser.add_argument("--formulary", action="store_true", help="use the stocked formulary")
    parser.add_argument(
        "--limit", type=int, default=25, help="max drugs; openFDA is 1 000 requests/day per IP"
    )
    args = parser.parse_args()

    rxcuis = list(args.rxcui)
    if args.formulary:
        rxcuis += formulary_rxcuis(args.limit)
    if not rxcuis:
        print("nothing to do: pass --rxcui or --formulary", file=sys.stderr)
        return 2

    drugs, written = run(rxcuis[: args.limit])
    print(f"\n{written} signal(s) across {drugs}/{len(rxcuis[:args.limit])} drug(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
