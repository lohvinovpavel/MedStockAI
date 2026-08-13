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

from google import genai
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .ai_tasks import TASKS
from .config import settings
from .db import SessionLocal
from .models import AICache

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Built on first use, not at import. Five of the seven services have no
    Gemini key and must still be able to `import medstock_shared`."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def dedupe_key(task: str, payload: dict) -> str:
    """Stable hash of the question. Same question, same key, same answer —
    this is what makes retries free and re-asking cheap."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{task}\x00{canonical}".encode()).hexdigest()


class AIError(RuntimeError):
    pass


class _Retryable(Exception):
    """429 or 5xx — worth another attempt. Anything else is our bug, not theirs."""


@retry(
    retry=retry_if_exception_type(_Retryable),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _generate_json(prompt: str) -> dict:
    try:
        response = _get_client().models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "http_options": {"timeout": int(settings.llm_timeout_seconds * 1000)},
            },
        )
    except Exception as exc:  # google-genai raises typed errors; status is what we branch on
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if status == 429 or (isinstance(status, int) and status >= 500):
            raise _Retryable(str(exc)) from exc
        raise
    return json.loads(response.text)


def ask_ai(task_name: str, payload: dict) -> dict:
    """Ask Gemini and get an answer back, synchronously. Raises `AIError` on
    any failure — a 429/5xx after retries, a bad response, or a task whose
    `validate()` rejects the output. Catch it; a model outage must degrade
    your endpoint, not 500 it.

    Anything volatile in `payload` (a timestamp, a request id) defeats the
    cache — every call becomes a miss and a charge. To force a fresh answer,
    include something that actually changed, e.g. `shortage_event.updated_at`.
    """
    task = TASKS[task_name]
    key = dedupe_key(task_name, payload)

    with SessionLocal() as s:
        cached = s.execute(
            select(AICache.result).where(AICache.type == task_name, AICache.dedupe_key == key)
        ).scalar_one_or_none()
        if cached is not None:
            return cached

    try:
        result = _generate_json(task.prompt.format(**payload))
        if task.validate:
            task.validate(result)  # a bad citation fails here — it never reaches a pharmacist
    except Exception as exc:
        raise AIError(str(exc)) from exc

    with SessionLocal() as s:
        s.execute(
            insert(AICache)
            .values(type=task_name, dedupe_key=key, result=result)
            .on_conflict_do_nothing(constraint="uq_ai_cache_type_dedupe")
        )
        s.commit()
    return result
