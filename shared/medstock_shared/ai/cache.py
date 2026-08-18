"""`ai_cache` read/write, and the `ai_audit_log` write. Split out of `core.py`
so the retry/breaker logic there doesn't have to scroll past SQL to get to
the point.

Keyed on (type, prompt_version, dedupe_key): bumping a task's prompt_version
in ai_tasks.py is what invalidates its own cache. Without that column an
edited prompt would keep serving answers the old prompt produced -- see
docs/ai-module-plan.md §2.
"""

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal
from ..models import AIAuditLog, AICache

_log = logging.getLogger(__name__)


def cache_get(task_name: str, prompt_version: str, key: str) -> dict | None:
    """Cache miss on any DB error (including a missing ai_cache table)."""
    session = SessionLocal()
    try:
        return session.execute(
            select(AICache.result).where(
                AICache.type == task_name,
                AICache.prompt_version == prompt_version,
                AICache.dedupe_key == key,
            )
        ).scalar_one_or_none()
    except SQLAlchemyError:
        session.rollback()
        _log.exception("ai_cache read failed; treating as miss")
        return None
    finally:
        session.close()


def cache_put(
    task_name: str, prompt_version: str, model_name: str, key: str, result: dict
) -> None:
    session = SessionLocal()
    try:
        session.execute(
            insert(AICache)
            .values(
                type=task_name,
                prompt_version=prompt_version,
                model_name=model_name,
                dedupe_key=key,
                result=result,
            )
            .on_conflict_do_nothing(constraint="uq_ai_cache_type_promptver_dedupe")
        )
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        _log.exception("ai_cache write failed; returning uncached result")
    finally:
        session.close()


def write_audit(
    *,
    hospital_id: str | None,
    actor_id: str,
    request_id: str,
    task_type: str,
    dedupe_key: str,
    prompt_version: str,
    model_name: str,
    outcome: str,
    latency_ms: int,
    tools_called: list | None = None,
) -> None:
    """Best-effort, same fail-open posture as cache_get/cache_put above: an
    audit write failure must not turn into a 500 for the request it is
    describing. `ask_ai()` calls this exactly once per call, after the
    outcome (cache_hit / live / breaker_open / error) is already decided.

    `tools_called` is copilot-only (docs/ai-module-plan.md Phase 4); every
    other caller leaves it `None` and gets the column's `[]` default."""
    values: dict = {
        "hospital_id": hospital_id,
        "actor_id": actor_id,
        "request_id": request_id,
        "task_type": task_type,
        "dedupe_key": dedupe_key,
        "prompt_version": prompt_version,
        "model_name": model_name,
        "outcome": outcome,
        "latency_ms": latency_ms,
    }
    if tools_called is not None:
        values["tools_called"] = tools_called

    session = SessionLocal()
    try:
        session.execute(insert(AIAuditLog).values(**values))
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        _log.exception("ai_audit_log write failed")
    finally:
        session.close()
