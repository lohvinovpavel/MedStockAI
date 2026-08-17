# Data Handling Strategy

Two kinds of data flow through this system and they want **opposite** strategies.
That is not a quirk to work around; it falls out of who owns the data and what
breaks when you get it wrong.

| | Patient data | Compliance data (FDA, RxNorm) |
|---|---|---|
| Owner | The hospital, and the person | The public |
| Cost of holding it | A breach, a BAA, a notification duty | Disk |
| Cost of *not* holding it | Almost nothing — the rules need features, not identity | A third party's uptime decides whether a shelf renders |
| Therefore | **Hold as little as possible** | **Hold as much as possible** |

Everything below is that one table, worked out.

---

## Part 1 — Patient data

### 1.1 Four classes, and only one is dangerous

| Class | Example | Where it may live |
|---|---|---|
| **Identifiers** | name, MRN, date of birth, address, phone | **Never ours.** Stripped at the hospital edge |
| **Clinical features** | age band, eGFR band, allergy codes, active RxCUIs | In a request body, in memory, for the length of one call |
| **Decision record** | who assessed what, which ruleset, what verdict | Persisted, tenant-scoped, indefinitely |
| **Reference** | interaction rules, PGx guidelines, ADR signals | Persisted, global, no tenant scope |

The whole strategy is that **the second row never becomes the third**. A feature
vector is used and discarded; what persists is that a decision was made, not who
it was about.

### 1.2 The lifecycle of one assessment

```
hospital EHR
  │  de-identify at the edge (their side, our published contract)
  ▼
feature vector ──HTTPS──▶ patient-profiling
                            │  PatientVector.from_json drops unrecognised keys
                            │  rules run in memory
                            │  request body is never logged
                            ▼
                          response ──▶ caller
                            │
                            └─▶ assessment_log:  request_id, actor_id,
                                                 feature_hash, ruleset_version,
                                                 verdict
                                                 (no vector, no identifiers)
```

`feature_hash` is the load-bearing trick: it proves *what was asked* without
storing the answer's inputs. Two identical assessments hash identically, so the
log can show a repeat without keeping a second copy of the clinical picture.

`PatientVector.from_json` is not a convenience — it is the boundary. It reads
the nine fields the contract names and ignores everything else, so a hospital
that sends `full_name` gets it dropped rather than stored. That is verified in
`services/patient-profiling/tests/test_assess.py`, not asserted in prose.

### 1.3 Retention

| Data | Retention | Why |
|---|---|---|
| Feature vector | **Zero.** Never written | Nothing to breach, nothing to expire |
| `assessment_log` | Indefinite | It is the compliance artefact. It holds no patient, so keeping it costs nothing and destroying it would be the loss |
| `patient_ref` (if adopted) | Indefinite, opaque | Continuity without identity — only if we never hold the mapping |
| Request/response bodies in logs | **Never written** | Retrofitting redaction after PHI arrives means auditing every log line ever emitted |

There is no deletion workflow for patient data because there is no patient data.
That is the point: a right-to-erasure request is answered by pointing at the
schema.

### 1.4 What would change if PHI were ever accepted

Recorded so the answer is not improvised later:

1. Patient tables become tenant class with RLS, not reference.
2. `ask_ai()` moves to Vertex AI under a BAA, **or** patient data is barred from
   the model path entirely. The current `ai_cache` has no `hospital_id` by
   design, so a PHI-class task would leak across hospitals by construction.
3. CMEK on the database, key rotation, access review, breach-notification runbook.
4. `assessment_log` gains a retention clock (HIPAA's six years).

See [phi-readiness.md](phi-readiness.md) for the seams that make 1 and 2 a
configuration change rather than a rewrite.

---

## Part 2 — Compliance data: query or ingest?

### 2.1 The rule

> **If it colours something a user is looking at, ingest it.
> If it answers a question about a drug nobody stocks, query it.**

A badge on a stock page must not depend on openFDA being up. `GET /status`
serves a whole page of stock from one indexed read in ~11 ms; the same page
built from live API calls would be as slow as the slowest third party and would
fail when they deploy. That is not a performance preference, it is the
difference between a shelf that renders and one that does not.

The inverse is equally firm. There are 136,942 products and a hospital stocks a
few thousand. Pre-fetching the certification of every drug on earth so that one
pharmacist can look up one unusual analogue is work nobody asked for.

### 2.2 What the feeds actually are

Measured against the live `download.json` manifest on 2026-08-14:

| Feed | Records | Bulk size | On the request path? | Strategy |
|---|---|---|---|---|
| **NDC Directory** | 136,942 | **26.7 MB, 1 file** | yes — the badge | **Ingest, bulk** |
| **Enforcement** (recalls) | 17,866 | 3.8 MB, 1 file | yes — the badge | **Ingest, bulk** |
| **Drug shortages** | 1,636 | 0.4 MB, 1 file | yes — the badge | **Ingest, bulk** |
| **Drugs@FDA** | 29,267 | 8.9 MB, 1 file | not yet | Ingest when a rule needs it |
| **SPL labels** | 261,732 | 1.8 GB, 14 files | no — offline rule extraction | Query, or bulk weekly |
| **FAERS events** | 20.7 M | **113 GB, 1,767 files** | no | **Never bulk.** Precompute aggregates |
| **RxNorm NDC status** | per-NDC | n/a | no — COMP-2 only | **Query**, cache 7 days |

### 2.3 The finding that changes the current design

`services/ingest/app/certification.py` pages the API, and pages are capped:
`skip` stops at 25,000 against 136,942 products, so **a full sync by pagination
is impossible**. That is why the job runs in targeted mode, certifying only
what is on a shelf.

The bulk manifest removes the constraint entirely. The whole directory is **one
26.7 MB zip, re-exported daily, served from a CDN and not counted against the
1,000 requests/day API quota.** Three downloads a day — directory, enforcement,
shortages — total about **31 MB** and give complete coverage of everything that
drives a badge.

**Recommendation: move the three badge feeds to bulk download.** Targeted mode
stays as the fallback for a fresh environment or a single-drug refresh, but it
stops being the only thing that works.

### 2.4 The budget, either way

openFDA allows 1,000 requests/day **per IP**, shared across every job and every
on-demand call.

| Activity | Requests/day |
|---|---|
| Bulk downloads (3 feeds) | **0** — CDN, not the API |
| Targeted certification of a 50-NDC shelf | ~5 |
| Recall + shortage paging (if not bulk) | ~4 |
| COMP-2 exploration | 1 per unknown NDC (RxNorm is a separate host) |
| **Headroom for exploration** | ~990 |

The budget was never the binding constraint. The `skip` ceiling was.

### 2.5 Freshness, and what each source is allowed to conclude

| Source | Refresh | May set red? |
|---|---|---|
| NDC Directory | daily | yes — expired listing, ended marketing |
| Enforcement | daily | yes — ongoing Class I |
| Shortages | daily | no — supply is yellow, not a legality failure |
| RxNorm NDC status | on demand, 7-day TTL | yes — obsolete NDC |
| News (not built) | on demand | **never** — unverified claims raise yellow only |

Red requires a government source. That is structural, not stylistic: an
unconfirmed report warrants "check this", which is yellow.

### 2.6 Caching rules for on-demand answers

COMP-2 writes `provenance='on_demand'` with `expires_at = now + 7 days`.

- A **scheduled** row has a CronJob behind it and never expires on its own.
- An **on-demand** row has nothing refreshing it, so it must expire itself, or a
  recall issued next week is invisible forever.
- A stale row is re-explored on read, not on a timer — nobody should pay to
  refresh a drug nobody has asked about since.

### 2.7 When a feed is down

| Failure | Behaviour |
|---|---|
| openFDA down, badge already ingested | Unaffected. This is the entire argument for ingesting |
| openFDA down, COMP-2 exploration | Per-NDC error in the response; the other NDCs still answer |
| RxNorm down | Exploration falls back to the directory alone; `NDC_UNRESOLVED` rather than a guess |
| Bulk download truncated | The job fails and the CronJob retries. Yesterday's rows stay — stale beats wrong |
| `drug_certification` missing | `503`, never green. A clean bill from a check that never ran is worse than no answer |

---

## Part 3 — The one-line version

**Patient data: hold nothing, prove everything.** The vector is used and
discarded; the log records that a decision happened, not who it was about.

**Compliance data: hold everything, refresh daily.** It is public, it is 31 MB,
and a pharmacist's screen should never wait on a government API.
