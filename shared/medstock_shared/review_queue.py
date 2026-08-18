"""The label-derived risk-profile review queue -- promoted out of
services/patient-profiling/app/main.py (DR-3, docs/ai_workflow_impl_plan.md)
so the copilot's `list_review_queue` tool reads the same query
`GET /risk-profiles` does.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import engine
from .models import DrugRiskProfile

PROFILE_STATUSES = ("awaiting_approval", "approved", "rejected")
MAX_QUEUE = 200


def profile_dict(row: DrugRiskProfile) -> dict:
    return {
        "id": row.id,
        "rxcui": str(row.rxcui),
        "reaction": row.reaction,
        "seriousness": row.seriousness,
        # The reviewable basis. A queue that showed a verdict without the
        # factors and the quote would be asking for a signature on nothing.
        "risk_factors": list(row.risk_factors or []),
        "citation": row.citation or "",
        "section": row.section or "",
        "spl_id": row.spl_id,
        "status": row.status,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_note": row.review_note or "",
        "extracted_at": row.extracted_at.isoformat() if row.extracted_at else None,
    }


def accept_rate(counts: dict[str, int]) -> float | None:
    """Approved as a share of everything ruled on. Profiles still awaiting
    review are excluded -- counting them as failures would make the rate
    start at zero and climb as reviewing happens, which measures the
    reviewer's progress rather than the model's accuracy. `None` while
    nothing has been ruled on: a rate over no decisions is unknown, not 0.0.
    """
    ruled = counts.get("approved", 0) + counts.get("rejected", 0)
    return round(counts.get("approved", 0) / ruled, 3) if ruled else None


def load_queue(status: str, rxcui: str | None, limit: int) -> tuple[list[dict], dict[str, int]]:
    """The queue and the tally behind it, separated from any endpoint so the
    response shape is testable without a Postgres to point at."""
    with Session(engine) as session:
        query = select(DrugRiskProfile)
        if status != "all":
            query = query.where(DrugRiskProfile.status == status)
        if rxcui:
            query = query.where(DrugRiskProfile.rxcui == str(rxcui))
        # Oldest first: a queue that shows the newest extraction first leaves
        # the backlog sitting at the bottom for ever.
        rows = session.scalars(query.order_by(DrugRiskProfile.extracted_at).limit(limit)).all()
        # Over the whole table, not the page -- this is what the accept rate
        # is computed from, and a rate over one page of 50 is not the rate.
        tally = session.execute(
            select(DrugRiskProfile.status, func.count()).group_by(DrugRiskProfile.status)
        ).all()
    counts = {s: 0 for s in PROFILE_STATUSES} | {str(s): int(n) for s, n in tally}
    return [profile_dict(r) for r in rows], counts


def apply_review(profile_id: int, updates: dict) -> tuple[str, dict] | None:
    """Write a ruling. Returns (status before, the row after), or None if
    there is no such profile."""
    with Session(engine) as session:
        row = session.get(DrugRiskProfile, profile_id)
        if row is None:
            return None
        previous = row.status
        for column, value in updates.items():
            setattr(row, column, value)
        session.commit()
        session.refresh(row)
        return previous, profile_dict(row)
