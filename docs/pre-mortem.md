# Pre-Mortem

*It is defence day. The project did not go well. Working backwards: why?*

Every claim below is checked against the repository or a live source, not
recalled. Ranked by how likely it is to actually be the cause.

---

## 1. The three things the architecture is proudest of do not exist

Checked against a migrated database on 2026-08-14:

```
audit_log_entry table ....... does not exist
RLS policies ................ 0
database triggers ........... 0
review_decision ............. does not exist
recommendation .............. does not exist
```

[services.md](services.md) §1.3 says *"the audit log writes itself"* and that
append-only is **a grant, not a convention** — demonstrable in ten seconds at
defence. §1.2 says tenant isolation is *"set, never filtered"*, enforced by RLS
rather than application code. [phi-readiness.md](phi-readiness.md) §8 lists both
as HIPAA controls already satisfied.

None of it is built. `session_scope()` faithfully sets `app.hospital_id` on
every transaction and **no policy reads it**. Today any service can read any
hospital's rows.

This is the single largest risk, because it is not a missing feature — it is the
gap between what the documents argue and what the code does, in exactly the
areas an examiner will probe. The product's stated compliance value is *"every
decision and its rationale is already logged"*. There is no table to log into.

**Cost to fix:** one migration with `CREATE POLICY`, one `write_audit_entry()`
trigger, one `REVOKE`. Perhaps a day. It is not hard; it is unstarted.

## 2. The core promise has no data behind it

The product brief's one-sentence pitch is warning *days in advance* that a drug
will run out. Days-of-supply is called "the core metric of the whole product".

It needs consumption history. There is no source for it — [services.md](services.md)
§8 #3 lists it as open, `prediction` is a `/healthz` stub with **1 test**, and
no public feed provides hospital usage. Everything actually built reasons about
*supply-side* risk: is this drug certified, recalled, in shortage, safe for this
patient. That is a real product. It is not the one the brief promises.

**Either** the pitch changes to match what exists — "we tell you which of your
drugs are at risk and what you can safely switch to" — **or** a synthetic usage
generator lands and the forecast becomes real. Presenting the current system
under the current pitch invites the one question there is no answer to.

## 3. AI is the assignment's requirement and is barely present

`shared/medstock_shared/ai_tasks.py` registers exactly one task, `analogue`.
`extract`, `prediction` and `mapping_spec` are a comment.

Both services I built are deliberately model-free, and I would defend that
choice — determinism is worth more than intelligence on a safety path. But
"our system is explainable because it does not use AI" is an awkward answer to
a brief whose §4 requires explainability, grounding, human approval and audit
logging *of AI output*.

The strongest available answer is already designed and not built: the AI reads
FDA label prose into checkable rules, offline, human-approved before use
([patient-pipeline.md](patient-pipeline.md) §9). That single feature converts
"we avoided AI" into "we used AI where it is defensible and nowhere else".

**Also unverified:** `gemini_model = "gemini-3.5-flash"` in `config.py`. Nobody
has confirmed that model id resolves. If it does not, every AI path 404s at the
worst possible moment.

## 4. The demo depends on a live third party

`compliance` reads from its own tables, so a badge survives openFDA being down —
that was designed for. But **COMP-2 exploration calls openFDA and RxNorm
synchronously**, and the seeded scenarios were built against feeds that move
daily.

Concretely, on defence day: openFDA rate-limits at 1,000 requests/day per IP,
shared by everyone on the network. A room full of people on one conference
Wi-Fi, plus a CI run, plus a rehearsal, is a plausible way to spend it.

**Mitigation is cheap:** run every scenario once the morning of, so every badge
is already in the table, and never demonstrate `/explore` live without a
pre-explored NDC as the fallback.

## 5. Coverage is wildly uneven, and CI has never run

```
compliance          62 tests
analogue            48
patient-profiling   34
ingest              22
auth                 9
inventory            5
warehouse            1
prediction           1
```

`warehouse` and `prediction` are one health check each. Two of the seven
services are names on a diagram.

Worse: `.github/workflows/ci.yml` has `pull_request` and `push` **commented
out** — it is `workflow_dispatch` only. So nothing has ever gated a merge. The
whole-repo `pytest` was broken at collection until this week, and 24 lint errors
sit in `main` right now. A test suite nobody runs is a document, not a check.

**The broken-window case in point:** `auth`'s `test_login_round_trip` has been
red long enough to be normal. Everyone assumed a missing keypair. It is not — it
is a `Secure` cookie that no client will replay over plain `http`, so the test
can never pass as written. A red test that everyone has learned to ignore is how
the *next* real failure gets ignored too.

## 6. Nine services, one package name

Every service ships a top-level package called `app`, and uv installs all nine
into one virtualenv. A bare `import app` resolves to whichever was installed
last. The root `conftest.py` now swaps `sys.modules` per service to make the
suite collectable — which works, and is a workaround for a naming decision that
should be fixed at the source.

It has already produced one subtle failure: purging the modules instead of
swapping them broke 20 analogue tests, because they patch by string
(`monkeypatch.setattr("app.main.search_concepts", …)`) and the patch landed on a
freshly-imported module while the TestClient kept the old one. That class of bug
is expensive to diagnose and will recur.

## 7. The distributed monolith tax is being paid daily

Four developers, seven services, one `shared/` package. §0 states the cost
plainly: *a change to `shared/` redeploys all seven*. This session alone touched
`shared/` five times, and merging `main` produced conflicts in two services'
`main.py` because deployment work and feature work edited the same files.

Not fatal — but with four people and a deadline, the merge cost rises as
everyone converges on the same files near the end.

## 8. Compliance risk: the signal may not deserve attention

Measured over 3,000 live openFDA products:

- **0.0% red.** The directory lists currently-marketed products, so "expired
  listing" describes a state it mostly does not publish.
- 12.5% yellow, of which **372 of 375 were one rule** — a permanent attribute of
  homeopathic products.

The category/transient split fixed the second problem, cutting the alert surface
from 375 to 5. The first is structural: without recalls and RxNorm obsolescence,
red is nearly unreachable. Both are now wired in, and a demo shelf of 52 drugs
produced **2 red, 5 yellow**. It works — but it took measurement to discover, and
the same trap applies to every rule added later. The lesson is that thresholds
must be simulated over time, not reasoned about.

The clearest example: a "listing expires within 90 days" rule looked sensible and
would have turned **73% of a formulary amber every October**, because 70.5% of
products share one annual expiry date. Only a two-year time-travel simulation
caught it.

## 9. Regulatory framing could be attacked

Two claims are strong and two are soft.

**Strong:** no PHI is held; the de-identification boundary is enforced in code
and tested. Nothing in the request path calls a model.

**Soft:** the FDA CDS exemption (§520(o)(1)(E)) depends on the clinician being
able to *independently review the basis*. That holds for the deterministic rules
— every finding names a source. It would stop holding the moment a model output
reaches a clinician without a reviewable citation. And EU MDR Rule 11 is a much
narrower carve-out than the US one; if anyone asks about European deployment,
the honest answer is "Class IIa, notified body, out of scope".

Do not claim HIPAA compliance. Claim, accurately, that the design keeps the
system out of scope, and that [phi-readiness.md](phi-readiness.md) documents what
changes when it is not.

## 10. Nobody has shown this to a pharmacist

No hospital partner, no real formulary, no clinician review of the weight table
in [patient-pipeline.md](patient-pipeline.md) §4 — which is a *clinical* opinion
encoded as arithmetic, written by engineers. "Two moderate concerns reach amber"
is a defensible-sounding sentence that no pharmacist has agreed to.

One conversation with one hospital pharmacist would either validate the whole
premise or reveal that the workflow is wrong, and it is the cheapest
de-risking available.

---

## If only three things get done

1. **Build the audit log and RLS.** One migration. It converts the two loudest
   claims in the documentation from aspiration into a ten-second demonstration,
   and it is the difference between "we designed for compliance" and "here it
   is".
2. **Turn CI on.** Uncomment `pull_request`. A suite that does not gate a merge
   is decoration, and the repository already contains the evidence of what that
   costs.
3. **Decide what the pitch is** — advance warning of shortages, or supply-side
   risk with safe substitution. Both are good products. Only the second one is
   built, and defending the first with the second is the fastest way to lose
   the room.

## What is genuinely solid

Worth saying, because a pre-mortem reads bleak by construction:

- The certification signal is measured, not assumed — 20,000 randomised
  invariant checks, a two-year time-travel simulation, and real openFDA data end
  to end.
- The deterministic pipeline is fully explainable and reproducible: same input,
  same verdict, same reasons, every time.
- The no-PHI boundary is enforced at the parser and verified by test, not
  promised in prose.
- COMP-2 found a genuinely better second source (RxNorm) by measuring three
  candidates rather than guessing, and it resolves 100% of the gap.
- The purchasing plan connects patient safety to buying decisions in a way the
  brief never asked for and a pharmacy director would actually use.
