"""How much does the single-shot extraction actually lose?

The graph in `services/ingest/app/prognosis_graph.py` costs six to eight model
calls per drug where the single-shot path costs one. The case for that spend is
the factors single-shot drops -- it discards anything it cannot express in the
collected vocabulary, without counting or recording it. Nobody knows the rate,
which means nobody can say whether the graph is worth running.

This measures it without spending anything. Every `prognosis` answer the model
has ever given is in `ai_cache`, so the drops can be recomputed from real output
by replaying it through the validator. As more extractions run the number gets
better on its own.

Read it as a decision, not a report:

* a low drop rate says single-shot is fine and the graph is not worth 8x;
* a high rate says the graph is recovering real clinical conditions;
* a high rate concentrated in `egfr_band`/`age_band` says the *prompt* should be
  fixed first, which is one call, not eight.

    python scripts/prognosis_drop_report.py
    python scripts/prognosis_drop_report.py --verbose
"""

from __future__ import annotations

import argparse
import collections
import sys

from medstock_shared.db import SessionLocal
from sqlalchemy import text

sys.path.insert(0, "services/ingest")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="list every dropped factor")
    args = parser.parse_args()

    # Imported here, after the sys.path line above puts services/ingest on it.
    from app.prognosis_graph import validate_risks

    with SessionLocal() as session:
        rows = session.execute(
            text("select dedupe_key, result from ai_cache where type = 'prognosis'")
        ).all()

    if not rows:
        print("no cached prognosis extractions yet.")
        print("run: python -m app.prognosis --formulary --limit 20   (from services/ingest)")
        return 1

    risks_in = factors_in = 0
    kept_risks = kept_factors = 0
    reasons: collections.Counter[str] = collections.Counter()
    features: collections.Counter[str] = collections.Counter()
    details: list[str] = []

    for _key, result in rows:
        risks = result.get("risks") or []
        source = result.get("source_text") or ""
        risks_in += len(risks)
        factors_in += sum(len(r.get("risk_factors") or []) for r in risks)

        partition = validate_risks(risks, source)
        kept_risks += len(partition.kept)
        kept_factors += sum(len(r["risk_factors"]) for r in partition.kept)

        for rejection in partition.rejected:
            # The reason carries the offending value; strip it so the counter
            # groups by *kind* of failure rather than by one label's wording.
            kind = rejection.reason.split("(")[0].strip()
            reasons[kind] += 1
            feature = (rejection.factor or {}).get("feature")
            if feature:
                features[str(feature)] += 1
            if args.verbose:
                details.append(f"  {rejection.reaction[:40]:42} {rejection.reason}")

    lost_factors = factors_in - kept_factors
    lost_risks = risks_in - kept_risks
    pct = (lost_factors / factors_in * 100) if factors_in else 0.0

    print(f"{len(rows)} cached extraction(s)\n")
    print(f"  risks    {risks_in:4}  ->  {kept_risks:4} kept   ({lost_risks} lost)")
    print(f"  factors  {factors_in:4}  ->  {kept_factors:4} kept   ({lost_factors} lost, {pct:.1f}%)")

    if reasons:
        print("\nwhy factors were dropped:")
        for reason, count in reasons.most_common():
            print(f"  {count:4}  {reason}")
    if features:
        print("\nwhich features the model could not express:")
        for feature, count in features.most_common():
            print(f"  {count:4}  {feature}")
    if details:
        print("\ndropped:")
        print("\n".join(details))

    print("\n--- what this means -------------------------------------------------")
    if factors_in == 0:
        print("  nothing to judge yet.")
    elif pct == 0:
        print("  Nothing is being dropped. The graph's repair round has nothing to")
        print("  recover, so its 6-8 calls per drug buy only the reviewer's account.")
        print("  Do not switch the default on this evidence.")
    elif pct < 10:
        print(f"  {pct:.1f}% lost. Low. Try fixing the single-shot prompt first --")
        print("  one call, not eight. Revisit the graph if that does not move it.")
    else:
        print(f"  {pct:.1f}% lost, and each is a condition the label stated. A profile")
        print("  missing a condition matches MORE patients than the label describes,")
        print("  so this is a safety argument, not an accuracy one. The graph earns")
        print("  its calls.")
    if len(rows) < 20:
        print(f"\n  Caveat: {len(rows)} extraction(s) is not a sample. Run more before deciding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
