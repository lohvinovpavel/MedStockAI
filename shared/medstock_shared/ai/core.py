"""Direct, synchronous Gemini calls with a shared cache table and an
in-process circuit breaker -- no queue, no separate service. `ai-handler` was
removed: at this system's volume (tens of calls/hour) the queue's real
benefits -- crash-survival mid-call, backpressure -- weren't in use, while
the dedup/cache win is just as reachable as a shared table. See
docs/services.md §4 and docs/ai-module-plan.md for the full trade-off writeup.

Callers: `from medstock_shared import ask_ai, AIError`. A plain function call
from a normal (non-async) FastAPI route -- FastAPI runs it in its threadpool,
so it does not block the event loop.
"""

import hashlib
import json
import logging
import time
import uuid

from google import genai
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..ai_tasks import TASKS
from ..auth import Principal
from ..config import settings
from .breaker import CircuitBreaker
from .cache import cache_get, cache_put, write_audit

# Offline callers (ingest's CronJobs) have no Principal -- there is no user
# on the request, just a public FDA label. Attributable, not anonymous.
_SYSTEM_ACTOR = "system:ingest"

_log = logging.getLogger(__name__)
_client: genai.Client | None = None

# One breaker per process, shared by every task -- see breaker.py's docstring
# for why a per-task breaker would be the wrong shape for this failure.
_breaker = CircuitBreaker()


def shared_breaker() -> CircuitBreaker:
    """The process-wide breaker, for callers that talk to Gemini without
    going through `ask_ai()` -- today, only the copilot's own streaming
    calls (services/analogue/app/copilot.py), which aren't cacheable and so
    can't use `ask_ai` directly, but still hit the same provider and must
    trip the same breaker.

    Named `shared_breaker`, not `breaker`: this module already has a
    `breaker` submodule (`from .breaker import CircuitBreaker` above), and a
    same-named function here would shadow that submodule as an attribute of
    this module -- exactly the bug this comment is here so nobody
    reintroduces."""
    return _breaker


def client() -> genai.Client:
    """Public alias of `_get_client()`, for the same non-`ask_ai` callers
    `shared_breaker()` is for -- one lazily-built client per process either
    way."""
    return _get_client()


def _get_client() -> genai.Client:
    """Built on first use, not at import. Five of the seven services have no
    Gemini key and must still be able to `import medstock_shared`. Belt and
    braces alongside __init__.py's lazy __getattr__: this also protects a
    direct `import medstock_shared.ai`, which bypasses that wrapper."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def dedupe_key(task: str, payload: dict) -> str:
    """Stable hash of the question. Same question, same key, same answer --
    this is what makes retries free and re-asking cheap."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{task}\x00{canonical}".encode()).hexdigest()


class AIError(RuntimeError):
    pass


class _Retryable(Exception):
    """429 only. 5xx is an outage -- fail so the caller can degrade this request."""


@retry(
    retry=retry_if_exception_type(_Retryable),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _generate_json(prompt: str, timeout_seconds: float | None = None) -> dict:
    try:
        response = _get_client().models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "http_options": {
                    "timeout": int((timeout_seconds or settings.llm_timeout_seconds) * 1000),
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
        # Everything else here is a 5xx or a timeout -- an outage, not a
        # bad-input error -- so it is what trips the breaker.
        _breaker.record(False)
        raise
    _breaker.record(True)
    return json.loads(response.text)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def ask_ai(
    task_name: str,
    payload: dict,
    *,
    principal: Principal | None = None,
    request_id: str | None = None,
) -> dict:
    """Ask Gemini and get an answer back, synchronously. Raises `AIError` on
    any failure -- a 429 after retries, a 5xx, a timeout, an open breaker, or
    a task whose `validate()` raises. Catch it; a model outage must degrade
    your endpoint, not 500 it. Cache read/write errors are misses, not AI
    failures.

    Anything volatile in `payload` (a timestamp, a request id) defeats the
    cache -- every call becomes a miss and a charge. To force a fresh answer,
    include something that actually changed, e.g. `shortage_event.updated_at`.

    `principal` is what makes the call auditable -- omit it only for ingest's
    offline CronJobs, which have no user on the request; every request-path
    caller (analogue) should pass the JWT principal it already verified. One
    `ai_audit_log` row is written per call, regardless of outcome.
    """
    task = TASKS[task_name]
    key = dedupe_key(task_name, payload)
    request_id = request_id or uuid.uuid4().hex
    started = time.monotonic()

    def _audit(outcome: str) -> None:
        write_audit(
            hospital_id=principal.hospital_id if principal else None,
            actor_id=principal.user_id if principal else _SYSTEM_ACTOR,
            request_id=request_id,
            task_type=task_name,
            dedupe_key=key,
            prompt_version=task.prompt_version,
            model_name=settings.gemini_model,
            outcome=outcome,
            latency_ms=_elapsed_ms(started),
        )

    cached = cache_get(task_name, task.prompt_version, key)
    if cached is not None:
        _audit("cache_hit")
        return cached

    if not _breaker.allow():
        # No network call at all -- this is the point of the breaker.
        _audit("breaker_open")
        raise AIError(f"circuit breaker open for task {task_name!r}")

    try:
        result = _generate_json(task.prompt.format(**payload), task.timeout_seconds)
        # Validate citations against the caller's source, not Gemini's echo.
        if isinstance(result, dict) and payload.get("source_text"):
            result = {**result, "source_text": payload["source_text"]}
        if task.validate:
            task.validate(result)  # a hallucinated citation is stripped, not cached as a quote
    except Exception as exc:
        _audit("error")
        raise AIError(str(exc)) from exc

    cache_put(task_name, task.prompt_version, settings.gemini_model, key, result)
    _audit("live")
    return result
