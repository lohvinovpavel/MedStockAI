# C4 — AI analogue ranking

**Service:** `analogue` · **Flows:** 7, 19 · **Status:** ✅ implemented — the one registered AI task

`_filter_full_with_ai()` in `services/analogue/app/main.py`, calling `ask_ai("analogue", …)`.
`GET /analogues/ai-status` reports whether the filter is active.

This is the pattern every future AI feature in the system should copy, and it is worth stating
why it is correct:

- The model is a **closed-world filter**, not a generator. It receives candidates produced by
  RxNorm graph traversal and keeps about five. Any rxcui it returns that is not already in
  `by_rxcui` is discarded — it cannot invent a drug.
- `_citation_must_be_verbatim` in `shared/medstock_shared/ai_tasks.py` **strips** a hallucinated
  quote rather than rejecting the whole answer. Raising would fail `ask_ai`, drop back to the
  unfiltered list, and look like the AI did nothing; stripping keeps the filter and loses only
  the bad citation.
- Any failure falls back to the unfiltered RxNorm list. The feature degrades to "less helpful",
  never to "broken" — the `AIError` contract in `ask_ai`'s docstring.
- Results are cached in `ai_cache` on `(type, dedupe_key)`, global and deliberately not
  tenant-scoped: the payload is reference data, never PHI.

**No implementation work.** Two follow-ups owned elsewhere:

- [ ] H2 adds `model` and `prompt_version` to the key and the row. Until then a prompt edit
      silently keeps serving old answers.
- [ ] C5 adds the per-facility availability overlay without changing rank order.

**Do not** move ranking to a scoring model, and do not let the model produce `match_score` as a
free number — the score must remain derivable from equivalence class and source.
