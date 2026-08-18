"""Read path over `ai_audit_log` -- written on every copilot turn
(`_write_copilot_audit`) and, before this, never read back by anything.
`audit:read` exists in `PERMS` and guarded nothing (docs/ai_workflow_impl_plan.md
P4/DR-4).

No RLS policy on this table (docs/services.md §8 tracks that gap once, same
as `AssessmentLog`), so tenant scoping here is a hand-written predicate, not
`session_scope`. `hospital_id` is nullable -- `ingest`'s offline CronJobs
attribute to `'system:ingest'` with no hospital -- so a hospital's query
never sees those rows, and they never see a hospital's.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import engine
from .models import AIAuditLog

# An audit page, not a bulk export -- the aggregates below already answer
# "how many/how often"; this is just enough raw rows to spot-check one.
RECENT_LIMIT = 10


def _percentile(sorted_values: list[int], pct: float) -> int | None:
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, int(len(sorted_values) * pct))
    return sorted_values[idx]


def query_ai_decisions(
    hospital_id: str,
    days: int = 30,
    task_type: str | None = None,
    outcome: str | None = None,
) -> dict:
    """Aggregate, not dump -- a month of copilot turns is far more rows than
    a turn's context can hold. Counts by outcome, the tools called most
    often, an error rate, latency percentiles, and a handful of recent rows."""
    since = datetime.now(UTC) - timedelta(days=max(1, days))
    with Session(engine) as session:
        stmt = select(AIAuditLog).where(
            AIAuditLog.hospital_id == hospital_id, AIAuditLog.created_at >= since
        )
        if task_type:
            stmt = stmt.where(AIAuditLog.task_type == task_type)
        if outcome:
            stmt = stmt.where(AIAuditLog.outcome == outcome)
        rows = session.scalars(stmt.order_by(AIAuditLog.created_at.desc())).all()

    by_outcome = Counter(r.outcome for r in rows)
    tool_counts: Counter[str] = Counter()
    for r in rows:
        for call in r.tools_called or []:
            name = call.get("name") if isinstance(call, dict) else None
            if name:
                tool_counts[name] += 1
    latencies = sorted(int(r.latency_ms) for r in rows)

    total = len(rows)
    return {
        "window_days": days,
        "total": total,
        "by_outcome": dict(by_outcome),
        "top_tools": tool_counts.most_common(10),
        "latency_ms": (
            {"p50": _percentile(latencies, 0.50), "p95": _percentile(latencies, 0.95)}
            if total
            else None
        ),
        "error_rate": round(by_outcome.get("error", 0) / total, 3) if total else None,
        "recent": [
            {
                "request_id": r.request_id,
                "actor_id": r.actor_id,
                "task_type": r.task_type,
                "outcome": r.outcome,
                "latency_ms": r.latency_ms,
                "tools_called": r.tools_called,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows[:RECENT_LIMIT]
        ],
    }
