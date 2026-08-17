# C2 — Package lookup

**Service:** `analogue` · **Flow:** 7 · **Status:** ✅ implemented

`GET /drugs/{rxcui}/packages` in `services/analogue/app/main.py:126`. Step 2 of drug identity:
the chosen clinical concept (RxCUI) to the shelf ids (NDCs) that B2's stock read and C4's
ranking both need.

This endpoint is the bridge between the system's two key spaces — everything clinical is keyed
on RxCUI, everything physical on NDC — which is why `rxnorm_edge` has no foreign key to `drug`
in [db-schema.md](../db-schema.md).

**No implementation work.** Verify:

- [ ] An RxCUI with no packages returns an empty list, not 404.
- [ ] The NDC format returned matches what `stock_snapshot.ndc` stores — a hyphenation mismatch
      here silently breaks every stock join downstream.
