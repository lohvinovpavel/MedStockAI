"""Card contracts and projector registry for copilot chat UI.

Cards are pure projections of tool results (the model never authors a card).
This file defines the card contracts in Pydantic and the projector registry
that maps tool outputs into typed card payloads.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from ..auth import Principal

_log = logging.getLogger(__name__)

# In-memory proposal store for HITL confirmations (15 minute TTL)
_PROPOSALS: dict[str, dict[str, Any]] = {}


class Coverage(BaseModel):
    """Why an empty list is empty. Visual empty state distinction."""
    checked: int
    total: int | None = None
    window: str | None = None
    source_note: str | None = None


class DrugRef(BaseModel):
    rxcui: str | None = None
    ndc: str | None = None
    name: str | None = None


class CardBase(BaseModel):
    kind: str
    tool: str
    request_id: str
    coverage: Coverage | None = None
    provenance: dict[str, Any] | None = None


class AnalogueRow(BaseModel):
    rxcui: str
    name: str
    ndcs: list[str] = []
    primary_ndc: str | None = None
    matchScore: float | None = None
    quantity: int = 0
    in_stock: bool = False
    status: str = "unknown"
    codes: list[str] = []


class AnaloguesCard(CardBase):
    kind: Literal["analogues"] = "analogues"
    query_rxcui: str | None = None
    query_name: str | None = None
    items: list[AnalogueRow]
    truncated: bool = False


class CertificateCard(CardBase):
    kind: Literal["certificate"] = "certificate"
    ndc: str
    name: str | None = None
    status: str
    codes: list[str] = []
    reasons: list[str] = []
    ruleset_version: str | None = None
    sources_consulted: dict[str, bool] | None = None
    findings: list[dict[str, Any]] = []


class SweepCard(CardBase):
    kind: Literal["sweep"] = "sweep"
    status_filter: str = "attention"
    checked: int = 0
    flagged: list[dict[str, Any]] = []
    unknown: list[str] = []
    by_facility: dict[str, list[dict[str, Any]]] | None = None
    hospital_total: dict[str, Any] | None = None
    truncated: bool = False


class StockCard(CardBase):
    kind: Literal["stock"] = "stock"
    ndc: str | None = None
    rxcui: str | None = None
    name: str | None = None
    total_quantity: int = 0
    locations: list[dict[str, Any]] = []


class StorageExcursionCard(CardBase):
    kind: Literal["excursions"] = "excursions"
    facility_id: int | str | None = None
    window_hours: int = 24
    checked: int = 0
    excursions: list[dict[str, Any]] = []
    locations_monitored: int = 0
    locations_reporting: int = 0
    readings_checked: int = 0
    truncated: bool = False


class AtRiskCard(CardBase):
    kind: Literal["at_risk"] = "at_risk"
    facility_id: int | str | None = None
    within_days: int = 30
    surge_pct: int = 100
    run_id: str | None = None
    data_through: str | None = None
    skus_evaluated: int = 0
    checked: int = 0
    items: list[dict[str, Any]] = []
    truncated: bool = False
    note: str | None = None


class PatientRegimenCard(CardBase):
    kind: Literal["patient_regimen"] = "patient_regimen"
    age_band: str | None = None
    blood_group: str | None = None
    allergy_codes: list[str] = []
    condition_codes: list[str] = []
    pgx_phenotypes: list[str] = []


class SafetyAssessmentCard(CardBase):
    kind: Literal["safety_assessment"] = "safety_assessment"
    patient_ref: str | None = None
    rxcui: str
    drug_name: str | None = None
    verdict: str
    hard_stop: bool = False
    score: float = 0.0
    findings: list[dict[str, Any]] = []
    stock_available: int | None = None
    cert_status: str | None = None


class AssessmentExplainCard(CardBase):
    kind: Literal["assessment_explain"] = "assessment_explain"
    assessment_request_id: str
    overall_score: float = 0.0
    verdict: str = "pass"
    contributions: list[dict[str, Any]] = []
    ruleset_version: str | None = None


class ForecastCard(CardBase):
    kind: Literal["forecast"] = "forecast"
    ndc: str
    run_id: str | None = None
    model_version: str | None = None
    points: list[dict[str, Any]] = []


class ForecastStalenessCard(CardBase):
    kind: Literal["forecast_staleness"] = "forecast_staleness"
    has_run: bool = False
    run_id: str | None = None
    data_through: str | None = None
    generated_at: str | None = None
    note: str | None = None


class ReviewQueueCard(CardBase):
    kind: Literal["review_queue"] = "review_queue"
    status: str = "awaiting_approval"
    queue_total: int = 0
    counts: dict[str, int] = {}
    accept_rate: float | None = None
    most_urgent: list[dict[str, Any]] = []


class AuditSummaryCard(CardBase):
    kind: Literal["audit_summary"] = "audit_summary"
    window_days: int | None = None
    total: int | None = None
    by_outcome: dict[str, int] | None = None
    top_tools: list[Any] | None = None
    latency_ms: dict[str, Any] | None = None
    error_rate: float | None = None
    recent: list[dict[str, Any]] | None = None
    single_record: dict[str, Any] | None = None


class DrugSearchCard(CardBase):
    kind: Literal["drug_search"] = "drug_search"
    query_name: str = ""
    matches: list[dict[str, Any]] = []
    ambiguous: bool = False


class ProposalCard(CardBase):
    kind: Literal["proposal"] = "proposal"
    proposal_id: str
    action: str = "draft_order"
    facility_id: int
    supplier_id: int
    supplier_name: str | None = None
    ndc: str
    drug_name: str | None = None
    quantity: int
    unit: str = "units"
    pack_size: int = 1
    est_total_cost: float | None = None
    coverage_days: int | None = None
    lead_time_days: int | None = None
    review_decision_id: int
    review_decision_valid: bool = True
    review_decision_note: str | None = None
    compliance_status: str = "green"
    compliance_codes: list[str] = []
    blocked: bool = False
    block_reason: str | None = None
    expires_at: str | None = None


class RunProposalCard(CardBase):
    kind: Literal["run_proposal"] = "run_proposal"
    proposal_id: str
    action: str = "forecast_run"
    facility_id: int | str | None = None
    last_run_at: str | None = None
    expires_at: str | None = None


# Projector Registry
_PROJECTORS: dict[str, Callable[[dict, Principal, str, dict | None], CardBase | None]] = {}


def register_projector(tool_name: str):
    def decorator(fn: Callable[[dict, Principal, str, dict | None], CardBase | None]):
        _PROJECTORS[tool_name] = fn
        return fn
    return decorator


@register_projector("search_analogues_rxnorm")
@register_projector("find_analogues")
def project_analogues(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    raw_items = result.get("items") or []
    items = []
    for item in raw_items:
        items.append(
            AnalogueRow(
                rxcui=str(item.get("rxcui") or ""),
                name=str(item.get("name") or ""),
                ndcs=item.get("ndcs") or [],
                primary_ndc=item.get("primary_ndc"),
                quantity=int(item.get("quantity") or 0),
                in_stock=bool(item.get("in_stock")),
            )
        )
    return AnaloguesCard(
        tool="search_analogues_rxnorm",
        request_id=request_id,
        query_rxcui=args.get("rxcui") if args else None,
        items=items,
        truncated=len(raw_items) >= 5,
    )


@register_projector("find_drug_by_name")
def project_drug_search(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    return DrugSearchCard(
        tool="find_drug_by_name",
        request_id=request_id,
        query_name=args.get("name", "") if args else "",
        matches=result.get("matches") or [],
        ambiguous=bool(result.get("ambiguous")),
    )


@register_projector("verify_batch_cert")
@register_projector("check_certificate")
def project_certificate(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    return CertificateCard(
        tool="verify_batch_cert",
        request_id=request_id,
        ndc=str(result.get("ndc") or (args.get("ndc") if args else "")),
        name=result.get("name"),
        status=str(result.get("status") or "unknown"),
        codes=result.get("codes") or [],
        reasons=result.get("reasons") or [],
        ruleset_version=result.get("ruleset_version"),
        findings=result.get("findings") or [],
    )


@register_projector("explore_ndc")
def project_explore(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    return CertificateCard(
        tool="explore_ndc",
        request_id=request_id,
        ndc=str(result.get("ndc") or (args.get("ndc") if args else "")),
        status=str(result.get("status") or "unknown"),
        codes=result.get("codes") or [],
        reasons=result.get("codes") or [],
        sources_consulted=result.get("sources_consulted"),
    )


@register_projector("sweep_shelf_certificates")
def project_sweep(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    checked = int(result.get("checked") or 0)
    flagged = result.get("flagged") or []
    unknown = result.get("unknown") or []
    coverage = Coverage(
        checked=checked,
        total=checked,
        source_note="No stocked drugs found with current filter" if checked == 0 else None,
    )
    return SweepCard(
        tool="sweep_shelf_certificates",
        request_id=request_id,
        coverage=coverage,
        status_filter=args.get("status_filter", "attention") if args else "attention",
        checked=checked,
        flagged=flagged,
        unknown=unknown,
        by_facility=result.get("by_facility"),
        hospital_total=result.get("hospital_total"),
        truncated=bool(result.get("truncated")),
    )


@register_projector("check_stock_by_ndc")
@register_projector("get_stock")
def project_stock(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    locations = result.get("locations") or []
    total = int(result.get("total_quantity") or result.get("quantity") or 0)
    return StockCard(
        tool="check_stock_by_ndc",
        request_id=request_id,
        ndc=result.get("ndc") or (args.get("ndc") if args else None),
        rxcui=result.get("rxcui") or (args.get("rxcui") if args else None),
        total_quantity=total,
        locations=locations,
    )


@register_projector("list_storage_excursions")
def project_excursions(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    checked = int(result.get("checked") or 0)
    monitored = int(result.get("locations_monitored") or 0)
    reporting = int(result.get("locations_reporting") or 0)
    readings = int(result.get("readings_checked") or 0)
    window = f"{result.get('window_hours', 24)}h"

    source_note = None
    if readings == 0 or monitored == 0:
        source_note = "No telemetry readings recorded for any location — this is not a clean result."
    elif checked == 0:
        source_note = f"All {monitored} monitored locations operating within acceptable temperature and humidity limits."

    coverage = Coverage(
        checked=readings,
        total=monitored,
        window=window,
        source_note=source_note,
    )
    return StorageExcursionCard(
        tool="list_storage_excursions",
        request_id=request_id,
        coverage=coverage,
        facility_id=args.get("facility_id") if args else None,
        window_hours=int(result.get("window_hours") or 24),
        checked=checked,
        excursions=result.get("excursions") or [],
        locations_monitored=monitored,
        locations_reporting=reporting,
        readings_checked=readings,
        truncated=bool(result.get("truncated")),
    )


@register_projector("list_at_risk_skus")
def project_at_risk(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    checked = int(result.get("checked") or 0)
    skus_evaluated = int(result.get("skus_evaluated") or checked)
    coverage = Coverage(
        checked=skus_evaluated,
        total=skus_evaluated,
        window=f"{args.get('within_days', 30) if args else 30} days",
        source_note="No forecast run has been generated" if not result.get("run_id") else None,
    )
    return AtRiskCard(
        tool="list_at_risk_skus",
        request_id=request_id,
        coverage=coverage,
        facility_id=args.get("facility_id") if args else None,
        within_days=int(args.get("within_days", 30) if args else 30),
        surge_pct=int(args.get("surge_pct", 100) if args else 100),
        run_id=result.get("run_id"),
        data_through=result.get("data_through"),
        skus_evaluated=skus_evaluated,
        checked=checked,
        items=result.get("items") or [],
        truncated=bool(result.get("truncated")),
        note=result.get("note"),
    )


@register_projector("get_patient_regimen")
def project_patient_regimen(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    return PatientRegimenCard(
        tool="get_patient_regimen",
        request_id=request_id,
        age_band=result.get("age_band"),
        blood_group=result.get("blood_group"),
        allergy_codes=result.get("allergy_codes") or [],
        condition_codes=result.get("condition_codes") or [],
        pgx_phenotypes=result.get("pgx_phenotypes") or [],
    )


@register_projector("assess_patient_for_drug")
def project_safety_assessment(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    return SafetyAssessmentCard(
        tool="assess_patient_for_drug",
        request_id=request_id,
        patient_ref=args.get("patient_id") if args else None,
        rxcui=str(args.get("rxcui") if args else ""),
        drug_name=result.get("drug_name"),
        verdict=str(result.get("verdict") or "pass"),
        hard_stop=bool(result.get("hard_stop")),
        score=float(result.get("score") or 0.0),
        findings=result.get("findings") or [],
        stock_available=result.get("stock_available"),
        cert_status=result.get("cert_status"),
    )


@register_projector("explain_assessment")
def project_explain(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    return AssessmentExplainCard(
        tool="explain_assessment",
        request_id=request_id,
        assessment_request_id=str(args.get("request_id") if args else ""),
        overall_score=float(result.get("overall_score") or 0.0),
        verdict=str(result.get("verdict") or "pass"),
        contributions=result.get("contributions") or [],
        ruleset_version=result.get("ruleset_version"),
    )


@register_projector("get_forecast")
def project_get_forecast(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    return ForecastCard(
        tool="get_forecast",
        request_id=request_id,
        ndc=str(result.get("ndc") or (args.get("ndc") if args else "")),
        run_id=result.get("run_id"),
        model_version=result.get("model_version"),
        points=result.get("points") or [],
    )


@register_projector("check_forecast_staleness")
@register_projector("propose_forecast_rerun")
def project_staleness(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    return ForecastStalenessCard(
        tool="check_forecast_staleness",
        request_id=request_id,
        has_run=bool(result.get("has_run")),
        run_id=result.get("run_id"),
        data_through=result.get("data_through"),
        generated_at=result.get("generated_at"),
        note=result.get("note"),
    )


@register_projector("list_review_queue")
def project_review_queue(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    total = int(result.get("queue_total") or 0)
    coverage = Coverage(
        checked=total,
        total=total,
        source_note="Review queue is completely empty" if total == 0 else None,
    )
    return ReviewQueueCard(
        tool="list_review_queue",
        request_id=request_id,
        coverage=coverage,
        status=str(args.get("status", "awaiting_approval") if args else "awaiting_approval"),
        queue_total=total,
        counts=result.get("counts") or {},
        accept_rate=result.get("accept_rate"),
        most_urgent=result.get("most_urgent") or [],
    )


@register_projector("query_ai_decisions")
def project_audit(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result:
        return None
    if result.get("found") is not None:
        return AuditSummaryCard(
            tool="query_ai_decisions",
            request_id=request_id,
            single_record=result,
        )
    return AuditSummaryCard(
        tool="query_ai_decisions",
        request_id=request_id,
        window_days=result.get("window_days"),
        total=result.get("total"),
        by_outcome=result.get("by_outcome"),
        top_tools=result.get("top_tools"),
        latency_ms=result.get("latency_ms"),
        error_rate=result.get("error_rate"),
        recent=result.get("recent"),
    )


@register_projector("propose_order")
@register_projector("draft_order")
def project_proposal(result: dict, principal: Principal, request_id: str, args: dict | None) -> CardBase | None:
    if "error" in result and result.get("error") not in ("compliance_blocked", "review_decision_mismatch"):
        return None
    return ProposalCard(
        tool="propose_order",
        request_id=request_id,
        proposal_id=str(result.get("proposal_id") or uuid.uuid4().hex),
        action="draft_order",
        facility_id=int(result.get("facility_id") or (args.get("facility_id") if args else 1)),
        supplier_id=int(result.get("supplier_id") or (args.get("supplier_id") if args else 1)),
        supplier_name=result.get("supplier_name"),
        ndc=str(result.get("ndc") or (args.get("ndc") if args else "")),
        drug_name=result.get("drug_name"),
        quantity=int(result.get("quantity") or (args.get("quantity") if args else 1)),
        unit=result.get("unit") or "units",
        pack_size=int(result.get("pack_size") or 1),
        est_total_cost=result.get("est_total_cost"),
        coverage_days=result.get("coverage_days"),
        lead_time_days=result.get("lead_time_days"),
        review_decision_id=int(result.get("review_decision_id") or (args.get("review_decision_id") if args else 0)),
        review_decision_valid=bool(result.get("review_decision_valid", True)),
        review_decision_note=result.get("review_decision_note"),
        compliance_status=str(result.get("compliance_status") or "green"),
        compliance_codes=result.get("compliance_codes") or [],
        blocked=bool(result.get("blocked", False)),
        block_reason=result.get("block_reason"),
        expires_at=result.get("expires_at"),
    )


def card_for(
    tool_name: str,
    result: dict,
    principal: Principal,
    request_id: str,
    args: dict | None = None,
) -> dict | None:
    """Project a tool result dict into a typed card dict.

    Returns None if no projector exists or if projection fails (degrading to prose).
    """
    proj = _PROJECTORS.get(tool_name)
    if proj is None:
        return None
    try:
        card = proj(result, principal, request_id, args)
        if card is None:
            return None
        return card.model_dump(mode="json")
    except Exception:
        _log.exception("card projection failed tool=%s", tool_name)
        return None


def store_proposal(proposal: dict[str, Any], principal: Principal | None = None) -> str:
    """Store a proposal with 15-minute TTL, shared across replicas via DB cache."""
    pid = proposal.get("proposal_id") or uuid.uuid4().hex
    now = datetime.now(UTC)
    proposal["proposal_id"] = pid
    proposal["expires_at"] = (now + timedelta(minutes=15)).isoformat()
    if principal:
        proposal["hospital_id"] = str(principal.hospital_id)
        proposal["user_id"] = str(principal.user_id)

    # In-memory update and sweep of expired proposals
    _PROPOSALS[pid] = proposal
    for k in list(_PROPOSALS.keys()):
        exp = _PROPOSALS[k].get("expires_at")
        if exp:
            try:
                if datetime.fromisoformat(exp) <= now:
                    _PROPOSALS.pop(k, None)
            except Exception:
                pass

    # Persistent DB cache so proposals survive pod restarts and cross replicas
    try:
        from .cache import cache_put
        cache_put("order_proposal", "v1", "copilot", pid, proposal)
    except Exception:
        pass
    return pid


def get_proposal(proposal_id: str) -> dict[str, Any] | None:
    """Retrieve an unexpired proposal by ID."""
    now = datetime.now(UTC)
    p = _PROPOSALS.get(proposal_id)
    if p:
        exp = p.get("expires_at")
        if exp:
            try:
                if datetime.fromisoformat(exp) > now:
                    return dict(p)
                _PROPOSALS.pop(proposal_id, None)
            except Exception:
                return dict(p)
        else:
            return dict(p)

    # DB fallback
    try:
        from .cache import cache_get
        data = cache_get("order_proposal", "v1", proposal_id)
        if data:
            exp_str = data.get("expires_at")
            if exp_str:
                if datetime.fromisoformat(exp_str) > now:
                    return data
            else:
                return data
    except Exception:
        pass
    return None


def consume_proposal(proposal_id: str) -> dict[str, Any] | None:
    """Consume an unexpired proposal for single-use confirmation."""
    p = get_proposal(proposal_id)
    if p is None:
        return None
    _PROPOSALS.pop(proposal_id, None)
    try:
        from ..db import SessionLocal
        from ..models import AICache
        from sqlalchemy import delete

        session = SessionLocal()
        try:
            session.execute(
                delete(AICache).where(
                    AICache.type == "order_proposal",
                    AICache.dedupe_key == proposal_id,
                )
            )
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()
    except Exception:
        pass
    return p
