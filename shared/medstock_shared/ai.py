"""Direct, synchronous Gemini calls with a shared cache table — no queue, no
separate service. `ai-handler` was removed: at this system's volume (tens of
calls/hour) the queue's real benefits — crash-survival mid-call, backpressure
— weren't in use, while the dedup/cache win is just as reachable as a shared
table. See docs/services.md §4 for the full trade-off writeup.

Callers: `from medstock_shared import ask_ai, AIError`. A plain function call
from a normal (non-async) FastAPI route — FastAPI runs it in its threadpool,
so it does not block the event loop.
"""

import hashlib
import json
import logging

from google import genai
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .ai_tasks import TASKS
from .config import settings
from .db import SessionLocal
from .models import AICache

_log = logging.getLogger(__name__)
_client = genai.Client(api_key=settings.gemini_api_key)


def dedupe_key(task: str, payload: dict) -> str:
    """Stable hash of the question. Same question, same key, same answer —
    this is what makes retries free and re-asking cheap."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{task}\x00{canonical}".encode()).hexdigest()


class AIError(RuntimeError):
    pass


class _Retryable(Exception):
    """429 only. 5xx is an outage — fail so the caller can degrade this request."""


@retry(
    retry=retry_if_exception_type(_Retryable),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _generate_json(prompt: str) -> dict:
    try:
        response = _client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "http_options": {
                    "timeout": int(settings.llm_timeout_seconds * 1000),
                    # SDK default is 3 attempts; stacking that on our 429
                    # retries (and on 503) blew past the Next.js 30s proxy.
                    "retry_options": {"attempts": 1},
                },
            },
        )
    except Exception as exc:  # google-genai raises typed errors; status is what we branch on
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if status == 429:
            raise _Retryable(str(exc)) from exc
        raise
    return json.loads(response.text)


def _cache_get(task_name: str, key: str) -> dict | None:
    """Cache miss on any DB error (including a missing ai_cache table)."""
    session = SessionLocal()
    try:
        return session.execute(
            select(AICache.result).where(AICache.type == task_name, AICache.dedupe_key == key)
        ).scalar_one_or_none()
    except SQLAlchemyError:
        session.rollback()
        _log.exception("ai_cache read failed; treating as miss")
        return None
    finally:
        session.close()


def _cache_put(task_name: str, key: str, result: dict) -> None:
    session = SessionLocal()
    try:
        session.execute(
            insert(AICache)
            .values(type=task_name, dedupe_key=key, result=result)
            .on_conflict_do_nothing(constraint="uq_ai_cache_type_dedupe")
        )
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        _log.exception("ai_cache write failed; returning uncached result")
    finally:
        session.close()


def ask_ai(task_name: str, payload: dict) -> dict:
    """Ask Gemini and get an answer back, synchronously. Raises `AIError` on
    any failure — a 429 after retries, a 5xx, a bad response, or a task whose
    `validate()` raises. Catch it; a model outage must degrade your endpoint,
    not 500 it. Cache read/write errors are misses, not AI failures.

    Anything volatile in `payload` (a timestamp, a request id) defeats the
    cache — every call becomes a miss and a charge. To force a fresh answer,
    include something that actually changed, e.g. `shortage_event.updated_at`.
    """
    task = TASKS[task_name]
    key = dedupe_key(task_name, payload)

    cached = _cache_get(task_name, key)
    if cached is not None:
        return cached

    try:
        result = _generate_json(task.prompt.format(**payload))
        # Validate citations against the caller's source, not Gemini's echo.
        if isinstance(result, dict) and payload.get("source_text"):
            result = {**result, "source_text": payload["source_text"]}
        if task.validate:
            task.validate(result)  # a hallucinated citation is stripped, not cached as a quote
    except Exception as exc:
        raise AIError(str(exc)) from exc

    _cache_put(task_name, key, result)
    return result
