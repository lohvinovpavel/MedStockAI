# D1 — Certificate status

**Service:** `compliance` · **Flow:** 6 · **Status:** ✅ implemented

`GET /status`, `GET /certificates/{ndc}`, `GET /ruleset` in `services/compliance/app/main.py`.
Reads `drug_certification` (unique on `ndc`) and `certification_finding` (unique on
`ndc, code, source_ref`), written by `services/ingest/app/certification.py`.

Two schema decisions worth preserving:

- `drug_certification.status` is **derived, never authored** — `compliance.app.rules.status_for()`
  computes it from the findings. It is stored so `GET /status` is one indexed read instead of a
  re-evaluation per request, exactly the pattern B4's stock rollup should follow.
- `ruleset_version` records which rules produced a stored colour, so the colour can still explain
  itself after the thresholds change. Change a threshold and the findings are **replayed**, not
  re-fetched from FDA.
- `source_ref` on findings is what makes re-running the CronJob an upsert rather than a duplicate,
  and what distinguishes two recalls of the same class on the same drug.

**No implementation work.** Verify:

- [ ] An NDC never polled returns `unknown`, not an error — D2 is the path that fills it.
- [ ] Changing a threshold in `rules.py` and replaying findings changes the colour without any
      network call.
