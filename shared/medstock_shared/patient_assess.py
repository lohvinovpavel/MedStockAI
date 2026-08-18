"""Patient-assessment DB orchestration -- promoted out of
services/patient-profiling/app/main.py (DOC-1/DOC-3, docs/ai_workflow_impl_plan.md).

The rules engine itself (`assess()`, `patient_row_to_vector()`, ...) already
lived in `shared/medstock_shared/patient.py` -- what was service-local was
the orchestration around it: fetch the patient, look up approved risk
profiles / PGx guidelines / ADR signals for the candidate drug, assess, and
write the decision-trail row. That sequence lives here now, once, so
`POST /cart-check` and the copilot's `assess_patient_for_drug` /
`explain_assessment` tools call the same code instead of two copies that can
drift.

**Non-negotiable, same as the route**: `assess()`'s verdict is the rules
engine's arithmetic. Nothing here recomputes or overrides it -- these
functions fetch inputs and log outputs, they never touch a score.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session

from .auth import Principal
from .db import engine, session_scope
from .models import AdrSignal, AssessmentLog, DrugRiskProfile, Patient, PgxGuideline
from .patient import (
    BANDS,
    RULESET_VERSION,
    WEIGHTS,
    AdrSignalRow,
    PatientVector,
    PgxRecommendation,
    RiskProfile,
    assess,
    avoided_ingredient_warnings,
    patient_row_to_vector,
)
from .rxnorm import RxNormError, ingredients_for_rxcui


def approved_profiles(rxcuis: list[str]) -> list[RiskProfile]:
    """Label-derived risk profiles for these drugs -- **approved ones only**.

    A missing table degrades to "no profiles" rather than failing the
    assessment: the deterministic stages are still perfectly valid without it.
    """
    if not rxcuis:
        return []
    try:
        with Session(engine) as session:
            rows = (
                session.execute(
                    select(DrugRiskProfile).where(
                        DrugRiskProfile.rxcui.in_(rxcuis),
                        DrugRiskProfile.status == "approved",
                    )
                )
                .scalars()
                .all()
            )
    except (ProgrammingError, SQLAlchemyError):
        return []
    return [
        RiskProfile(
            rxcui=str(r.rxcui),
            reaction=r.reaction,
            seriousness=r.seriousness,
            risk_factors=tuple(r.risk_factors or ()),
            citation=r.citation or "",
            section=r.section or "",
        )
        for r in rows
    ]


def pgx_for(rxcuis: list[str]) -> list[PgxRecommendation]:
    """CPIC guidelines for these drugs (Tier 3). Degrades to "no guidelines"
    if the table is missing, for the same reason as approved_profiles."""
    if not rxcuis:
        return []
    try:
        with Session(engine) as session:
            rows = session.scalars(select(PgxGuideline).where(PgxGuideline.rxcui.in_(rxcuis))).all()
    except (ProgrammingError, SQLAlchemyError):
        return []
    return [
        PgxRecommendation(
            rxcui=str(r.rxcui),
            gene=r.gene,
            phenotype=r.phenotype,
            recommendation=r.recommendation or "",
            implication=r.implication or "",
            classification=r.classification or "",
            evidence_level=r.evidence_level or "",
            action_required=bool(r.action_required),
        )
        for r in rows
    ]


def adr_signals_for(rxcuis: list[str]) -> list[AdrSignalRow]:
    """Precomputed FAERS ratios for these drugs (Tier 1). Degrades to "no
    signals" if the table is missing, like the other two feed readers."""
    if not rxcuis:
        return []
    try:
        with Session(engine) as session:
            rows = session.scalars(select(AdrSignal).where(AdrSignal.rxcui.in_(rxcuis))).all()
    except (ProgrammingError, SQLAlchemyError):
        return []
    return [
        AdrSignalRow(
            rxcui=str(r.rxcui),
            reaction=r.reaction,
            prr=float(r.prr or 0),
            ror=float(r.ror or 0),
            n_reports=int(r.n_reports or 0),
        )
        for r in rows
    ]


def record_assessment(principal: Principal, vector: PatientVector, results: list[dict]) -> str:
    """Write the decision trail row, and return the request id.

    **Fails the request if it cannot write.** An assessment that reaches a
    clinician (or a copilot answer) without a corresponding audit row is a
    silent hole -- the answer looks identical either way, which is worse than
    refusing.
    """
    request_id = str(uuid.uuid4())
    with session_scope(principal.hospital_id, principal.user_id) as session:
        session.add(
            AssessmentLog(
                hospital_id=principal.hospital_id,
                request_id=request_id,
                actor_id=principal.user_id,
                feature_hash=vector.feature_hash(),
                ruleset_version=RULESET_VERSION,
                result={
                    "assessments": [
                        {
                            "rxcui": r.get("rxcui"),
                            "verdict": r.get("verdict"),
                            "score": r.get("score"),
                            "findings": [
                                {
                                    "code": f.get("code"),
                                    "weight": f.get("weight"),
                                    "source": f.get("source"),
                                    "stage": f.get("stage"),
                                }
                                for f in (r.get("findings") or [])
                            ],
                        }
                        for r in results
                    ]
                },
            )
        )
    return request_id


def _finding_dict(f) -> dict:
    return {
        "code": f.code,
        "severity": str(f.severity),
        "weight": f.weight,
        "message": f.message,
        "source": f.source,
        "stage": f.stage,
    }


def assess_for_drug(principal: Principal, patient_id: str, rxcui: str) -> dict:
    """One patient, one candidate drug: fetch, assess, log. Same inputs and
    same audit trail `POST /cart-check` produces for one cart line -- this is
    the DOC-1 promotion, used by the copilot's `assess_patient_for_drug`."""
    try:
        patient_uuid = uuid.UUID(str(patient_id))
    except ValueError:
        return {"error": "patient_id must be a UUID"}

    with session_scope(principal.hospital_id, principal.user_id) as session:
        row = session.get(Patient, patient_uuid)
        if row is None or row.hospital_id != principal.hospital_id:
            return {"error": "patient not found"}
        vector = patient_row_to_vector(row)

    profiles = approved_profiles([rxcui])
    pgx = pgx_for([rxcui])
    adr = adr_signals_for([rxcui])
    assessment = assess(vector, rxcui, risk_profiles=profiles, pgx=pgx, adr_signals=adr)
    warnings = [_finding_dict(f) for f in assessment.findings]

    try:
        ingredients = ingredients_for_rxcui(rxcui)
    except RxNormError:
        ingredients = []
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

    result = {
        "rxcui": rxcui,
        "verdict": str(assessment.verdict),
        "score": assessment.score,
        "findings": [_finding_dict(f) for f in assessment.findings],
    }
    request_id = record_assessment(principal, vector, [result])

    return {
        "ruleset_version": RULESET_VERSION,
        "request_id": request_id,
        "rxcui": rxcui,
        "verdict": str(assessment.verdict),
        "score": assessment.score,
        "warnings": warnings,
        "risk_profiles_applied": len(profiles),
        "pgx_guidelines_applied": len(pgx),
        "adr_signals_applied": len(adr),
    }


def _band_for(score: int | None) -> dict | None:
    """Which band turned this score into this verdict, and what the next one
    is."""
    if score is None:
        return None
    applied = next((t, v) for t, v in reversed(BANDS) if score >= t)
    above = [(t, v) for t, v in BANDS if t > score]
    return {
        "from_score": applied[0],
        "verdict": str(applied[1]),
        "next_verdict": str(above[0][1]) if above else None,
        "points_to_next": (above[0][0] - score) if above else None,
    }


# Sentinels rather than exceptions -- both callers (the /explain route and
# the explain_assessment tool) turn "not found" into their own shape (404
# vs. a tool-result dict), so raising here would make one of them catch and
# re-wrap anyway.
NOT_FOUND = "not_found"
UNAVAILABLE = "unavailable"


def explain_assessment(principal: Principal, request_id: str) -> dict | str:
    """Why a logged assessment said what it said -- the arithmetic itself,
    since every stage is deterministic. Single source for both `GET
    /explain/{id}` and the copilot's `explain_assessment` tool
    (docs/ai_workflow_impl_plan.md DOC-3), so the two can never drift.

    Returns `NOT_FOUND` or `UNAVAILABLE` instead of raising -- the two
    callers render those differently (an HTTP status vs. a tool-result
    dict).
    """
    try:
        with session_scope(principal.hospital_id, principal.user_id) as session:
            row = session.scalars(
                select(AssessmentLog)
                .where(
                    AssessmentLog.request_id == request_id,
                    AssessmentLog.hospital_id == principal.hospital_id,
                )
                .limit(1)
            ).first()
            if row is None:
                return NOT_FOUND
            logged = {
                "request_id": row.request_id,
                "actor_id": row.actor_id,
                "feature_hash": row.feature_hash,
                "ruleset_version": row.ruleset_version,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "result": dict(row.result or {}),
            }
    except (ProgrammingError, SQLAlchemyError):
        return UNAVAILABLE

    current = logged["ruleset_version"] == RULESET_VERSION
    explained = []
    for entry in logged["result"].get("assessments") or []:
        findings = entry.get("findings") or []
        total = sum(int(f.get("weight") or 0) for f in findings)
        explained.append(
            {
                "rxcui": entry.get("rxcui"),
                "verdict": entry.get("verdict"),
                "score": entry.get("score"),
                "band": _band_for(entry.get("score")),
                "contributions": [
                    {
                        "code": f.get("code"),
                        "weight": f.get("weight"),
                        "stage": f.get("stage"),
                        "source": f.get("source"),
                        "share": (round(int(f.get("weight") or 0) / total, 3) if total else None),
                    }
                    for f in sorted(findings, key=lambda x: -int(x.get("weight") or 0))
                ],
                "blocked": entry.get("score") is None,
            }
        )

    return {
        "request_id": logged["request_id"],
        "assessed_by": logged["actor_id"],
        "assessed_at": logged["created_at"],
        "feature_hash": logged["feature_hash"],
        "ruleset_version": logged["ruleset_version"],
        "current_ruleset_version": RULESET_VERSION,
        "explained_with_original_ruleset": current,
        "caveat": None
        if current
        else (
            f"This assessment ran under ruleset {logged['ruleset_version']}; the current "
            f"ruleset is {RULESET_VERSION}. The contributions below are the ones that "
            "produced this answer and do not describe how the same patient would be "
            "assessed today."
        ),
        "assessments": explained,
        "ruleset": {
            "weights": WEIGHTS,
            "bands": [{"from_score": t, "verdict": str(v)} for t, v in BANDS],
        },
    }
