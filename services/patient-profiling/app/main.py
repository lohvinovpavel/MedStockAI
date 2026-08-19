"""patient-profiling — substitution safety, cohort demand, and physician patients.

Accepts a **de-identified feature vector** from the hospital for /assess and
/demand. The demo `patient` table is a deliberate PHI exception for the
prescription-cart capstone: CRUD stores name/DOB, and /cart-check maps the row
to a PatientVector before calling assess() (docs/phi-readiness.md posture
unchanged for the rules engine).
"""

import os
import uuid
from datetime import UTC, date, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException
from medstock_shared.auth import Principal, require
from medstock_shared.db import engine, session_scope
from medstock_shared.models import AssessmentLog, Patient, PrognosisAssumption
from medstock_shared.organs import impacts as organ_impacts
from medstock_shared.patient import (
    BANDS,
    RULESET_VERSION,
    WEIGHTS,
    PatientVector,
    assess,
    avoided_ingredient_warnings,
    class_from_ingredients,
    class_of,
    patient_row_to_vector,
    plan_demand,
    profile_avoided_ingredients,
    register_drug_class,
)
from medstock_shared.patient_assess import (
    NOT_FOUND,
    UNAVAILABLE,
    adr_signals_for,
    approved_profiles,
    explain_assessment,
    pgx_for,
    record_assessment,
)
from medstock_shared.review_queue import (
    MAX_QUEUE,
    PROFILE_STATUSES,
    accept_rate,
    apply_review,
    load_queue,
)
from medstock_shared.rxnorm import RxNormError, ingredients_for_rxcui
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session

app = FastAPI(title="patient-profiling")
patients = APIRouter()

MAX_CANDIDATES = 50
MAX_COHORT = 5000
MAX_CART_ITEMS = 40
BLOOD_GROUPS = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "unknown"}


class PatientCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    date_of_birth: date
    blood_group: str | None = None
    allergy_codes: list[str] = Field(default_factory=list)
    condition_codes: list[str] = Field(default_factory=list)
    # "GENE:phenotype", as the lab reports it. Tier 3 input.
    pgx_phenotypes: list[str] = Field(default_factory=list)


class PatientUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    date_of_birth: date | None = None
    blood_group: str | None = None
    allergy_codes: list[str] | None = None
    condition_codes: list[str] | None = None
    pgx_phenotypes: list[str] | None = None


class CartItem(BaseModel):
    rxcui: str = Field(min_length=1, max_length=32)
    name: str | None = None


class CartCheckBody(BaseModel):
    patient_id: uuid.UUID
    items: list[CartItem] = Field(default_factory=list)


class AnalogueCheckBody(BaseModel):
    patient_id: uuid.UUID
    candidates: list[CartItem] = Field(default_factory=list)
    # The cart line these are offered against. Recorded, not used in scoring:
    # a substitute is assessed on its own merits, not relative to what it
    # replaces, or a bad line would make a worse one look acceptable.
    replacing: str | None = Field(default=None, max_length=32)
    # What the patient is already on, minus the line being replaced. Without it
    # a substitute can only be judged in isolation, and "safe on its own" is not
    # the question a physician swapping one drug in a regimen is asking.
    regimen: list[CartItem] = Field(default_factory=list)


def _norm_codes(codes: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in codes or []:
        code = str(raw).strip().lower()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _norm_phenotypes(values: list[str] | None) -> list[str]:
    """Dedupe and trim, but **do not lowercase**, unlike `_norm_codes`.

    CPIC's vocabulary is mixed case ("Poor Metabolizer", "*57:01 positive") and
    these strings are shown to a clinician verbatim; flattening them would make
    a guideline value look like something we made up. Matching is casefolded on
    both sides, so storage case never affects whether a rule fires.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        value = str(raw).strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _norm_blood(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    bg = value.strip().upper().replace(" ", "")
    # Accept "A positive"-style loosely by normalizing common forms.
    aliases = {
        "A+": "A+",
        "A-": "A-",
        "B+": "B+",
        "B-": "B-",
        "AB+": "AB+",
        "AB-": "AB-",
        "O+": "O+",
        "O-": "O-",
        "UNKNOWN": "unknown",
    }
    if bg not in aliases and bg.lower() == "unknown":
        return "unknown"
    if bg not in BLOOD_GROUPS and bg not in aliases:
        raise HTTPException(
            status_code=422, detail=f"blood_group must be one of {sorted(BLOOD_GROUPS)}"
        )
    return aliases.get(bg, bg)


def _patient_dict(row: Patient) -> dict:
    return {
        "id": str(row.id),
        "hospital_id": row.hospital_id,
        "full_name": row.full_name,
        "date_of_birth": row.date_of_birth.isoformat(),
        "blood_group": row.blood_group,
        "allergy_codes": list(row.allergy_codes or []),
        "condition_codes": list(row.condition_codes or []),
        "pgx_phenotypes": list(row.pgx_phenotypes or []),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _finding_dict(f) -> dict:
    return {
        "code": f.code,
        "severity": str(f.severity),
        "weight": f.weight,
        "message": f.message,
        "source": f.source,
        "stage": f.stage,
    }


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependencies checked on purpose —
    a database blip must not get every pod restarted."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/version")
def version() -> dict[str, str]:
    """GIT_SHA is baked in at image build time (Dockerfile) — unset outside
    a built container, e.g. running locally from source. semver comes from
    the installed medstock-patient-profiling package (pyproject.toml), not the image."""
    try:
        semver = pkg_version("medstock-patient-profiling")
    except PackageNotFoundError:
        semver = "unknown"
    return {
        "service": "patient-profiling",
        "version": os.environ.get("GIT_SHA", "unknown"),
        "semver": semver,
    }


@app.get("/ruleset")
def get_ruleset(_: Principal = Depends(require("inventory:read"))) -> dict:
    """The weight table and the bands, published. A tool that will not show you
    how it scored something is a tool a pharmacist is right to distrust."""
    return {
        "version": RULESET_VERSION,
        "weights": WEIGHTS,
        "bands": [{"from_score": t, "verdict": str(v)} for t, v in BANDS],
        "notes": [
            "A hard gate (allergy, duplicate ingredient) blocks and produces no score.",
            "Findings marked info carry weight 0 and record what could not be checked.",
        ],
    }


REVIEW_ACTIONS = {"approve": "approved", "reject": "rejected"}
# A ceiling on /patients, not a page size. The picker asks for far fewer;
# this is what stops `?limit=100000` turning the demo PHI table into a bulk
# export with one query parameter.
MAX_PATIENT_PAGE = 200


class ReviewBody(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=2000)


def review_update(action: str, actor: str, note: str, now: datetime) -> dict:
    """The columns a ruling writes. Pure, so the rule that a rejection records
    its reviewer just as an approval does is testable without a database."""
    return {
        "status": REVIEW_ACTIONS[action],
        "reviewed_by": actor,
        "reviewed_at": now,
        "review_note": note.strip(),
    }


@patients.get("/risk-profiles")
def list_risk_profiles(
    status: str = "awaiting_approval",
    rxcui: str | None = None,
    limit: int = 50,
    _: Principal = Depends(require("profile:review")),
) -> dict:
    """The review queue: what a model has proposed and nobody has ruled on yet.

    `status=all` shows every profile, which is what makes an approval auditable
    after the fact rather than only actionable before it.

    Unlike `approved_profiles`, a missing table is a **503 here, not an empty
    list**. On the request path an absent migration should degrade to "no
    prognosis" and let the deterministic stages answer. On this path an empty
    list reads as "nothing to review" — a reviewer would close the page and the
    backlog would be invisible — so the failure has to be loud.
    """
    if status != "all" and status not in PROFILE_STATUSES:
        raise HTTPException(
            status_code=422, detail=f"status must be 'all' or one of {PROFILE_STATUSES}"
        )
    limit = max(1, min(int(limit), MAX_QUEUE))

    try:
        items, counts = load_queue(status, rxcui, limit)
    except (ProgrammingError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="risk profile table unavailable") from exc

    return {
        "status": status,
        "limit": limit,
        "items": items,
        "counts": counts,
        "accept_rate": accept_rate(counts),
    }


@patients.post("/risk-profiles/{profile_id}/review")
def review_risk_profile(
    profile_id: int,
    body: ReviewBody,
    principal: Principal = Depends(require("profile:approve")),
) -> dict:
    """Gate 3 of docs/prognosis-and-procurement.md §1.3, and the only way a
    profile ever reaches a screen.

    Re-ruling on a profile that was already decided is allowed, and deliberately
    so: a label changes, or an approval turns out to have been wrong, and
    withdrawing it must not require a database edit. The status it had before
    comes back in the response, so an approval being overturned is visible
    rather than inferred.
    """
    updates = review_update(body.action, principal.user_id, body.note, datetime.now(UTC))
    try:
        ruled = apply_review(profile_id, updates)
    except (ProgrammingError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="risk profile table unavailable") from exc
    if ruled is None:
        raise HTTPException(status_code=404, detail="risk profile not found")

    previous, profile = ruled
    return {"previous_status": previous, "profile": profile}


@app.post("/assess")
def post_assess(
    payload: dict = Body(...),
    principal: Principal = Depends(require("profile:assess")),
) -> dict:
    """One patient, one or more candidate drugs.

    Body: `{"patient": {…feature vector…}, "candidates": ["861007", …]}`
    """
    patient = PatientVector.from_json(payload.get("patient") or {})
    candidates = [str(c) for c in (payload.get("candidates") or []) if c]
    if not candidates:
        raise HTTPException(status_code=422, detail="candidates must not be empty")
    if len(candidates) > MAX_CANDIDATES:
        raise HTTPException(status_code=400, detail=f"at most {MAX_CANDIDATES} candidates")

    profiles = approved_profiles(candidates)
    pgx = pgx_for(candidates)
    adr = adr_signals_for(candidates)
    assessments = [
        assess(patient, rxcui, risk_profiles=profiles, pgx=pgx, adr_signals=adr)
        for rxcui in candidates
    ]
    results = [a.as_dict() for a in assessments]
    # Where on the body each result bears, so the front-end can shade organs
    # instead of asking a physician to read ten finding codes. Derived from the
    # findings this assessment produced and nothing else -- a drug's usual
    # target organ is not shaded unless a finding put it there.
    for result, assessment in zip(results, assessments, strict=True):
        shaded, unmapped = organ_impacts(assessment.findings, class_of(result["rxcui"]))
        result["organs"] = [i.as_dict() for i in shaded]
        # Named, not hidden: a diagram that omits a finding invites the reader
        # to believe the organs are the whole story.
        result["organs_unmapped"] = unmapped
    request_id = record_assessment(principal, patient, results)
    return {
        "ruleset_version": RULESET_VERSION,
        # Quote this back to dispute an answer; it is the key into assessment_log.
        "request_id": request_id,
        "patient_ref": patient.patient_ref,
        # Which anatomical figure the analogue view draws. Already on the
        # de-identified vector -- a band-like clinical fact, not an identifier --
        # and showing a female patient on a male figure is a small dishonesty in
        # a clinical view that costs nothing to avoid.
        "sex": patient.sex,
        # So a caller can tell "no label risk applies to this patient" apart from
        # "nobody has approved a profile for this drug yet".
        "risk_profiles_applied": len(profiles),
        # Same distinction for Tier 3: zero means no CPIC guideline covers these
        # drugs, not that the genotype was ignored.
        "pgx_guidelines_applied": len(pgx),
        "adr_signals_applied": len(adr),
        "results": results,
    }


# Worst first — a batch is as serious as its most serious line.
_VERDICT_RANK = ("blocked", "red", "amber", "green")


def _worst_verdict(result: dict) -> str | None:
    """The gravest verdict in one assessment batch.

    A blocked line has no score, so it cannot be ranked by number; ranking by
    name keeps "one of these four drugs is contraindicated" from being filed
    under the green of the other three.
    """
    seen = {
        str(a.get("verdict") or "").lower()
        for a in (result.get("assessments") or [])
        if a.get("verdict")
    }
    return next((v for v in _VERDICT_RANK if v in seen), None)


@patients.get("/assessments")
def list_assessments(
    limit: int = 25,
    principal: Principal = Depends(require("profile:explain")),
) -> dict:
    """The decision trail, newest first — every assessment this hospital made.

    `/explain/{request_id}` could always explain a decision, but only if you
    already knew its id, which nothing told you. That made the audit trail
    §1.3 describes real in the database and unreachable from anywhere else.
    This is the index that closes it.

    Scoped to the caller's hospital, and it carries **no patient identifier** —
    the same property the table is built on. What a reader gets is who asked,
    when, what came back, and a request id to ask why.
    """
    limit = max(1, min(int(limit), 200))
    try:
        with session_scope(principal.hospital_id, principal.user_id) as session:
            rows = session.scalars(
                select(AssessmentLog)
                .where(AssessmentLog.hospital_id == principal.hospital_uuid)
                .order_by(AssessmentLog.created_at.desc())
                .limit(limit)
            ).all()
            items = [
                {
                    "request_id": r.request_id,
                    "actor_id": r.actor_id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "ruleset_version": r.ruleset_version,
                    # A summary, not the decision: enough to pick a row to open.
                    "drugs": [
                        a.get("rxcui") for a in (dict(r.result or {}).get("assessments") or [])
                    ],
                    # The worst verdict in the batch is what a reader scans for.
                    "verdict": _worst_verdict(dict(r.result or {})),
                }
                for r in rows
            ]
    except (ProgrammingError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="assessment log unavailable") from exc

    return {"items": items, "limit": limit, "current_ruleset_version": RULESET_VERSION}


@patients.get("/explain/{request_id}")
def explain(
    request_id: str,
    principal: Principal = Depends(require("profile:explain")),
) -> dict:
    """Why a logged assessment said what it said.

    This is the use-cases doc's PP-3 — "pharmacist asks why" — and the reason
    §6 gives for the FDA CDS exclusion holding: criterion (d) requires that a
    professional can independently review the *basis* of a recommendation, and
    a bare risk score with no reviewable basis does not qualify.

    §7 sketched this around SHAP contributions, because it assumed a Tier 2
    model. There is no model on this path: every stage is deterministic, so the
    contributions are not estimated, they are **the arithmetic itself**. Each
    finding's weight, its share of the total, the band that turned the total
    into a colour, and how far the score sits from the next band.

    **The stored ruleset version is checked against the current one.** If they
    differ, this says so and refuses to pretend, because explaining a
    six-month-old decision with today's weights is exactly the lie §7 warns
    about — and it is a lie that would look like a perfectly good answer.

    The lookup and the contribution arithmetic live in
    `medstock_shared.patient_assess.explain_assessment` (promoted for DOC-3,
    docs/ai_workflow_impl_plan.md) so this route and the copilot's
    `explain_assessment` tool can never drift from each other.
    """
    result = explain_assessment(principal, request_id)
    if result == NOT_FOUND:
        raise HTTPException(status_code=404, detail="no such assessment")
    if result == UNAVAILABLE:
        raise HTTPException(status_code=503, detail="assessment log unavailable")
    return result


@app.post("/demand")
def post_demand(
    payload: dict = Body(...),
    _: Principal = Depends(require("profile:assess")),
) -> dict:
    """Cohort -> purchasing plan.

    Body: `{"cohort": [ …vectors… ], "candidates": [...], "on_hand": {rxcui: units},
            "unavailable": [rxcui], "units_per_patient": 30}`

    `unavailable` is the bridge from COMP-1: a drug that is red or in shortage is
    withdrawn from supply and its patients are re-routed to the option that is
    safest **for each of them**, not to one alternative picked for the cohort.
    """
    raw_cohort = payload.get("cohort") or []
    if len(raw_cohort) > MAX_COHORT:
        raise HTTPException(status_code=400, detail=f"cohort of at most {MAX_COHORT}")
    cohort = [PatientVector.from_json(p) for p in raw_cohort]
    candidates = [str(c) for c in (payload.get("candidates") or []) if c]
    if not cohort or not candidates:
        raise HTTPException(status_code=422, detail="cohort and candidates must not be empty")

    lines, unserved = plan_demand(
        cohort,
        candidates,
        on_hand={str(k): int(v) for k, v in (payload.get("on_hand") or {}).items()},
        unavailable=[str(u) for u in (payload.get("unavailable") or [])],
        units_per_patient=int(payload.get("units_per_patient") or 30),
    )

    return {
        "ruleset_version": RULESET_VERSION,
        "cohort_size": len(cohort),
        "unavailable": [str(u) for u in (payload.get("unavailable") or [])],
        "lines": [
            {
                "rxcui": line.rxcui,
                "on_therapy": line.on_therapy,
                "substitutes_for": line.substitutes_for,
                "eligible": line.eligible,
                "blocked": line.blocked,
                "flagged": line.flagged,
                "units_needed": line.units_needed,
                "on_hand": line.on_hand,
                "shortfall": line.shortfall,
                "block_reasons": line.reasons,
            }
            for line in sorted(lines.values(), key=lambda x: -x.units_needed)
        ],
        "total_shortfall": sum(line.shortfall for line in lines.values()),
        # Patients whose therapy was withdrawn and who have no tolerated in-class
        # alternative. This is the number that decides whether a shortage is a
        # purchasing problem or a clinical one.
        "unservable": unserved,
        "unservable_total": sum(unserved.values()),
    }


DEFAULT_SWITCH_RATE = 0.6


def assumption(name: str, fallback: float) -> tuple[float, str]:
    """One PP-4 assumption and the note explaining it.

    Falls back rather than failing if the table is missing, for the same reason
    approved_profiles does: the migration not having run is a deployment state,
    not a reason a director cannot see a forecast. The fallback matches the
    value the migration seeds, so a degraded read gives the same number rather
    than a quietly different one.
    """
    try:
        with Session(engine) as session:
            row = session.execute(
                select(PrognosisAssumption).where(PrognosisAssumption.name == name)
            ).scalar_one_or_none()
    except (ProgrammingError, SQLAlchemyError):
        return fallback, "assumption table unavailable — using the built-in default"
    if row is None:
        return fallback, "no row for this assumption — using the built-in default"
    return float(row.value), str(row.note or "")


@patients.post("/forecast")
def forecast(
    payload: dict = Body(...),
    _: Principal = Depends(require("profile:assess")),
) -> dict:
    """Cohort -> purchasing plan, plus where the therapy is heading (PP-4).

    Body: the `/plan` body, plus optional `switch_rate` and `horizon_days`.

    `/plan` answers *what are they on, and what is safe*. This adds *and where
    is that going*: two hospitals with the same headcount and the same current
    prescriptions still need different stock, because their patients differ
    (docs/prognosis-and-procurement.md §2.1).

    Every projected number here rests on `switch_rate`, which is assumed rather
    than measured, so the response echoes it back under `assumptions`. A
    forecast that does not say which of its inputs were chosen rather than
    observed is the one dishonest thing this design could do.
    """
    raw_cohort = payload.get("cohort") or []
    if len(raw_cohort) > MAX_COHORT:
        raise HTTPException(status_code=400, detail=f"cohort of at most {MAX_COHORT}")
    cohort = [PatientVector.from_json(p) for p in raw_cohort]
    candidates = [str(c) for c in (payload.get("candidates") or []) if c]
    if not cohort or not candidates:
        raise HTTPException(status_code=422, detail="cohort and candidates must not be empty")

    seeded_rate, note = assumption("switch_rate", DEFAULT_SWITCH_RATE)
    override = payload.get("switch_rate")
    if override is None:
        switch_rate, rate_source = seeded_rate, "prognosis_assumption"
    else:
        try:
            switch_rate = float(override)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="switch_rate must be a number") from None
        if not 0.0 <= switch_rate <= 1.0:
            raise HTTPException(status_code=422, detail="switch_rate must be between 0 and 1")
        rate_source, note = "request", "supplied per-request, overriding the stored assumption"

    profiles = approved_profiles(candidates)
    unavailable = [str(u) for u in (payload.get("unavailable") or [])]
    lines, unserved = plan_demand(
        cohort,
        candidates,
        on_hand={str(k): int(v) for k, v in (payload.get("on_hand") or {}).items()},
        unavailable=unavailable,
        units_per_patient=int(payload.get("units_per_patient") or 30),
        risk_profiles=profiles,
    )

    cohort_size = len(cohort)
    return {
        "ruleset_version": RULESET_VERSION,
        "cohort_size": cohort_size,
        "horizon_days": int(payload.get("horizon_days") or 90),
        "unavailable": unavailable,
        "lines": [
            {
                "rxcui": line.rxcui,
                "on_therapy": line.on_therapy,
                "substitutes_for": line.substitutes_for,
                "eligible": line.eligible,
                "blocked": line.blocked,
                "flagged": line.flagged,
                "at_risk": line.at_risk,
                "switch_in": line.switch_in,
                "cohort_fit": line.cohort_fit(cohort_size),
                "projected_patients": line.projected(switch_rate),
                "projected_units": line.projected(switch_rate)
                * int(payload.get("units_per_patient") or 30),
                "units_needed": line.units_needed,
                "on_hand": line.on_hand,
                "shortfall": line.shortfall,
                "block_reasons": line.reasons,
            }
            for line in sorted(lines.values(), key=lambda x: -x.units_needed)
        ],
        "total_shortfall": sum(line.shortfall for line in lines.values()),
        "unservable": unserved,
        "unservable_total": sum(unserved.values()),
        "risk_profiles_applied": len(profiles),
        # Everything below was chosen, not derived. Named so a reader can tell
        # which parts of the forecast are observation and which are assumption.
        "assumptions": {
            "switch_rate": {"value": switch_rate, "source": rate_source, "note": note},
        },
    }


@patients.get("/patients")
def list_patients(
    q: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("patient:read")),
) -> dict:
    """Patients for the prescribe picker — searched and bounded, never the lot.

    This used to return every patient, which was fine while a demo environment
    held eight of them. A seeded cohort is a thousand, and an unbounded list is
    then wrong twice over: it ships a thousand names and dates of birth to a
    browser that will show one, and it hands the physician a dropdown they
    cannot get to the bottom of.

    `total` is reported separately from `items` so the caller can say "12 of
    1008" rather than silently showing a truncated list as if it were the whole
    population — a cut-off list that looks complete is how someone concludes a
    patient is not in the system.
    """
    limit = max(1, min(int(limit), MAX_PATIENT_PAGE))
    term = (q or "").strip()

    with session_scope(principal.hospital_id, principal.user_id) as session:
        stmt = select(Patient).where(Patient.hospital_id == principal.hospital_id)
        if term:
            # Substring, case-insensitive, on the name only. Matching on date of
            # birth as free text would let "1978" enumerate a birth year, which
            # is a re-identification handle we have no reason to hand out.
            stmt = stmt.where(Patient.full_name.ilike(f"%{term}%"))

        total = session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = session.scalars(stmt.order_by(Patient.full_name).limit(limit)).all()
        return {
            "items": [_patient_dict(r) for r in rows],
            "total": int(total or 0),
            "limit": limit,
            "q": term,
        }


@patients.post("/patients", status_code=201)
def create_patient(
    body: PatientCreate,
    principal: Principal = Depends(require("patient:write")),
) -> dict:
    blood = _norm_blood(body.blood_group)
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = Patient(
            hospital_id=principal.hospital_id,
            full_name=body.full_name.strip(),
            date_of_birth=body.date_of_birth,
            blood_group=blood,
            allergy_codes=_norm_codes(body.allergy_codes),
            condition_codes=_norm_codes(body.condition_codes),
            pgx_phenotypes=_norm_phenotypes(body.pgx_phenotypes),
        )
        session.add(row)
        session.flush()
        return _patient_dict(row)


@patients.get("/patients/{patient_id}")
def get_patient(
    patient_id: uuid.UUID,
    principal: Principal = Depends(require("patient:read")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.get(Patient, patient_id)
        if row is None or row.hospital_id != principal.hospital_uuid:
            raise HTTPException(status_code=404, detail="patient not found")
        return _patient_dict(row)


@patients.patch("/patients/{patient_id}")
def update_patient(
    patient_id: uuid.UUID,
    body: PatientUpdate,
    principal: Principal = Depends(require("patient:write")),
) -> dict:
    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.get(Patient, patient_id)
        if row is None or row.hospital_id != principal.hospital_uuid:
            raise HTTPException(status_code=404, detail="patient not found")
        if body.full_name is not None:
            row.full_name = body.full_name.strip()
        if body.date_of_birth is not None:
            row.date_of_birth = body.date_of_birth
        if body.blood_group is not None:
            row.blood_group = _norm_blood(body.blood_group)
        if body.allergy_codes is not None:
            row.allergy_codes = _norm_codes(body.allergy_codes)
        if body.condition_codes is not None:
            row.condition_codes = _norm_codes(body.condition_codes)
        if body.pgx_phenotypes is not None:
            row.pgx_phenotypes = _norm_phenotypes(body.pgx_phenotypes)
        session.flush()
        return _patient_dict(row)


@patients.post("/cart-check")
def cart_check(
    body: CartCheckBody,
    principal: Principal = Depends(require("patient:read")),
) -> dict:
    """Physician cart: profile assess + RxNorm ingredient avoid-warnings.

    Warnings only — findings from assess and avoided ingredients are returned
    as `warnings` so the UI can badge lines without hard-blocking prescribe.
    """
    if len(body.items) > MAX_CART_ITEMS:
        raise HTTPException(status_code=400, detail=f"at most {MAX_CART_ITEMS} cart items")

    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.get(Patient, body.patient_id)
        if row is None or row.hospital_id != principal.hospital_uuid:
            raise HTTPException(status_code=404, detail="patient not found")
        vector = patient_row_to_vector(row)
        patient_payload = _patient_dict(row)

    avoided = profile_avoided_ingredients(vector)
    # One lookup for the whole cart, not one per line: approved_profiles takes a
    # list precisely so a ten-item cart costs a single query.
    cart_rxcuis = [item.rxcui.strip() for item in body.items if item.rxcui.strip()]
    profiles = approved_profiles(cart_rxcuis)
    pgx = pgx_for(cart_rxcuis)
    adr = adr_signals_for(cart_rxcuis)
    results: list[dict] = []
    # The whole-regimen view. Findings from every line, so the profile figure can
    # show one body carrying everything rather than N bodies to compare.
    regimen_findings: list = []
    regimen_classes: set[str] = set()
    # Ingredients first, for the whole cart. They are needed for the avoid-warning
    # check anyway, and resolving class from them has to happen before assess()
    # runs or the class-gated stages are skipped on a drug we could have classed.
    ingredients_by_rxcui: dict[str, list] = {}
    for item in body.items:
        rx = item.rxcui.strip()
        try:
            ingredients_by_rxcui[rx] = ingredients_for_rxcui(rx)
        except RxNormError:
            ingredients_by_rxcui[rx] = []
        if class_of(rx) is None and ingredients_by_rxcui[rx]:
            register_drug_class(
                rx,
                class_from_ingredients(
                    str(i.get("name") or "") for i in ingredients_by_rxcui[rx]
                ),
            )

    for item in body.items:
        rxcui = item.rxcui.strip()
        assessment = assess(vector, rxcui, risk_profiles=profiles, pgx=pgx, adr_signals=adr)
        # Surface all findings as warnings for the cart (demo: no hard block UI).
        warnings = [_finding_dict(f) for f in assessment.findings]

        exclude_ingredient: str | None = None
        exclude_ingredient_name: str | None = None
        ingredients = ingredients_by_rxcui.get(rxcui, [])
        if not ingredients:
            warnings.append(
                {
                    "code": "RXNORM_UNAVAILABLE",
                    "severity": "info",
                    "weight": 0,
                    "message": "RxNorm unavailable — ingredient check skipped",
                    "source": "rxnorm",
                    "stage": 4,
                }
            )

        for finding in avoided_ingredient_warnings(vector, rxcui, ingredients):
            warnings.append(_finding_dict(finding))
            # Prefer the first avoided ingredient hit for analogue filtering.
            if exclude_ingredient is None:
                for _code, ing_rxcui, ing_name in avoided:
                    if any(
                        str(i["rxcui"]) == ing_rxcui
                        or ing_name.lower() in str(i.get("name") or "").lower()
                        for i in ingredients
                    ):
                        exclude_ingredient = ing_rxcui
                        exclude_ingredient_name = ing_name
                        break

        shaded, unshaded = organ_impacts(assessment.findings, class_of(rxcui))
        results.append(
            {
                "rxcui": rxcui,
                "name": item.name,
                "verdict": str(assessment.verdict),
                "score": assessment.score,
                "warnings": warnings,
                "exclude_ingredient": exclude_ingredient,
                "exclude_ingredient_name": exclude_ingredient_name,
                "ingredients": ingredients,
                "organs": [i.as_dict() for i in shaded],
                "organs_unmapped": unshaded,
            }
        )
        regimen_findings.extend(assessment.findings)
        if class_of(rxcui):
            regimen_classes.add(class_of(rxcui))

    # Logged for the same reason /assess is: this produces a per-patient verdict
    # a physician acts on. The cohort endpoints (/demand, /forecast) are not
    # logged here — they answer a purchasing question about a group, not a
    # clinical decision about a person, and assessment_log is the clinical trail.
    request_id = record_assessment(principal, vector, results)

    # One body for the whole regimen. A drug class is passed only when the cart
    # is all one class -- a duplicate-class finding names the class that stacked,
    # and handing an arbitrary one to the mapping would attribute a stack to a
    # drug that did not cause it.
    only_class = next(iter(regimen_classes)) if len(regimen_classes) == 1 else None
    regimen_shaded, regimen_unmapped = organ_impacts(regimen_findings, only_class)

    return {
        "ruleset_version": RULESET_VERSION,
        "request_id": request_id,
        "patient": patient_payload,
        # For the profile figure. Which anatomical frame to draw, and where the
        # whole regimen lands, as opposed to each line separately.
        "sex": vector.sex,
        "regimen_organs": [i.as_dict() for i in regimen_shaded],
        "regimen_organs_unmapped": regimen_unmapped,
        "results": results,
        # Mirrors /assess. Zero here is meaningful: it distinguishes "no approved
        # profile covers this cart" from "the prognosis stage never ran", which
        # otherwise look identical from a response with no PP-3 findings in it.
        "risk_profiles_applied": len(profiles),
        "pgx_guidelines_applied": len(pgx),
        "adr_signals_applied": len(adr),
    }


@patients.post("/analogue-check")
def analogue_check(
    body: AnalogueCheckBody,
    principal: Principal = Depends(require("patient:read")),
) -> dict:
    """Assess analogue candidates against the patient who would receive them.

    The prescribe workspace already narrows analogues by the one ingredient
    `/cart-check` flagged, then ranks what is left by hospital stock. Nothing on
    that path looks at the patient again. So the candidate sitting at the top —
    the one "Replace with analogue" swaps in — is the one there is most of, and
    it can still carry a CPIC contraindication for this patient's phenotype, an
    approved label risk matching their age or eGFR, an ADR signal, or an allergy
    reached through a *different* ingredient than the excluded one.

    Substituting is a prescribing decision, so it gets the same eight stages,
    the same approved-only profile filter, and the same audit row as the cart it
    replaces a line in. A substitute assessed more loosely than the drug it
    replaces would make switching the way to get an unassessed drug to a patient.

    Results come back **in request order**, not ranked. The caller holds the
    stock figures this service never sees, and ordering candidates is a question
    about safety *and* supply; answering half of it here and calling it a
    ranking would hide which half.
    """
    candidates = [item.rxcui.strip() for item in body.candidates if item.rxcui.strip()]
    if not candidates:
        raise HTTPException(status_code=422, detail="candidates must not be empty")
    if len(candidates) > MAX_CANDIDATES:
        raise HTTPException(status_code=400, detail=f"at most {MAX_CANDIDATES} candidates")

    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.get(Patient, body.patient_id)
        if row is None or row.hospital_id != principal.hospital_id:
            raise HTTPException(status_code=404, detail="patient not found")
        vector = patient_row_to_vector(row)
        patient_payload = _patient_dict(row)

    # The organs the rest of the regimen already loads. Assessed with the same
    # eight stages as everything else -- a burden derived some cheaper way would
    # not agree with the figure the same screen draws.
    standing: dict[str, int] = {}
    regimen_rxcuis = [i.rxcui.strip() for i in body.regimen if i.rxcui.strip()]
    if regimen_rxcuis:
        r_profiles = approved_profiles(regimen_rxcuis)
        r_pgx = pgx_for(regimen_rxcuis)
        r_adr = adr_signals_for(regimen_rxcuis)
        for other in regimen_rxcuis:
            other_assessment = assess(
                vector, other, risk_profiles=r_profiles, pgx=r_pgx, adr_signals=r_adr
            )
            for impact in organ_impacts(other_assessment.findings, class_of(other))[0]:
                standing[impact.organ] = standing.get(impact.organ, 0) + impact.weight

    # One lookup for the whole candidate list, as /cart-check does — twenty
    # analogues must not become sixty queries.
    profiles = approved_profiles(candidates)
    pgx = pgx_for(candidates)
    adr = adr_signals_for(candidates)

    results: list[dict] = []
    for item in body.candidates:
        rxcui = item.rxcui.strip()
        if not rxcui:
            continue
        assessment = assess(vector, rxcui, risk_profiles=profiles, pgx=pgx, adr_signals=adr)
        shaded, unmapped = organ_impacts(assessment.findings, class_of(rxcui))
        results.append(
            {
                "rxcui": rxcui,
                "name": item.name,
                "verdict": str(assessment.verdict),
                "score": assessment.score,
                # Which stages actually ran. A candidate assessed without the
                # PGx stage is not the same as one that passed it, and only this
                # tells them apart.
                "stages_completed": list(assessment.stages_completed),
                "findings": [_finding_dict(f) for f in assessment.findings],
                # Where this candidate bears. Absent until now: the organ work
                # was added to /assess, not here, so the analogue list has been
                # rendering without a figure the whole time.
                "organs": [i.as_dict() for i in shaded],
                "organs_unmapped": unmapped,
                # What it ADDS to this patient, as opposed to how it scores on
                # its own. Stacking onto an organ the rest of the regimen
                # already loads counts for more than landing on a clear one --
                # that is the difference between "safe drug" and "safe for
                # her". Blunt on purpose: it orders a short list, it does not
                # claim a dose-response relationship.
                "added_burden": (assessment.score or 0)
                + sum(i.weight + standing.get(i.organ, 0) // 2 for i in shaded),
                "compounds": [i.organ for i in shaded if i.organ in standing],
            }
        )

    request_id = record_assessment(principal, vector, results)

    return {
        "ruleset_version": RULESET_VERSION,
        "request_id": request_id,
        "patient": patient_payload,
        # Echoed so a reader of the audit row can see what was being replaced,
        # not just what was offered.
        "replacing": body.replacing.strip() if body.replacing else None,
        # What "compounds" is measured against, so a reader can see the baseline
        # rather than trusting a number.
        "standing_burden": standing,
        "results": results,
        # Same three counters as /cart-check, and meaningful for the same reason:
        # zero distinguishes "no guideline covers these candidates" from "that
        # stage never ran", which look identical from the findings alone.
        "risk_profiles_applied": len(profiles),
        "pgx_guidelines_applied": len(pgx),
        "adr_signals_applied": len(adr),
    }


app.include_router(patients)
app.include_router(patients, prefix="/api/patients")
