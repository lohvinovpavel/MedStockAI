"""POST /copilot/chat -- streamed, RBAC-filtered Gemini function calling
(docs/ai-module-plan.md Phase 4).

Lives in `analogue`, not a ninth service: analogue already holds a Gemini key
and the RxNorm/stock logic the copilot's main tool wraps, and a whole
Deployment for one endpoint repeats the `ai-handler` mistake services.md §4
already recorded and undid. Split out only if copilot traffic diverges from
analogue's own.

This is the one `async def` route in the whole system (docs/ai-module-plan.md
§0.5) -- it holds an SSE connection open across a multi-turn tool-calling
loop, which a blocking thread cannot do cheaply. It talks to Gemini directly
via the SDK's async client, not through `ask_ai()` (nothing here is
cacheable), but shares `ask_ai()`'s process-wide circuit breaker: same
provider, same outage, same breaker (`medstock_shared.ai.breaker()`).

Automatic function calling is explicitly disabled. Every tool call is routed
through `medstock_shared.ai.tools.execute()`, which re-checks the caller's
permission and runs the tool in a threadpool -- see that module for why
`declarations_for()` alone is not the security boundary.

`draft_order` is the only writing tool and always creates `status: draft`
(I1). Nothing here places, cancels, or deletes an order.
"""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from google.genai import types
from medstock_shared.ai import client, shared_breaker, write_audit
from medstock_shared.ai.tools import ToolDenied, declarations_for, denied_tools_for, execute
from medstock_shared.auth import Principal, require
from medstock_shared.config import settings
from pydantic import BaseModel

copilot = APIRouter()
_log = logging.getLogger("analogue.copilot")

# One user turn can chain several tool calls (search, then verify each hit);
# this bounds the loop so a model that never stops calling tools can't hold
# the connection open forever.
_MAX_TOOL_ROUNDS = 6

_SYSTEM_INSTRUCTION = (
    "You are the MedStock AI assistant for a hospital pharmacist. Answer only "
    "from your tools' results and what the user tells you -- never invent an "
    "RxCUI, NDC, stock number, or certification status. If a tool returns no "
    "usable result, say so plainly rather than guessing."
)


class ChatMessage(BaseModel):
    role: str  # "user" | "model"
    text: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _ai_available() -> bool:
    return bool((settings.gemini_api_key or "").strip())


def _system_instruction_for(principal: Principal) -> str:
    """Base instruction plus a note naming any tool this role can't call.

    declarations_for() already keeps ungranted tools off the model's callable
    list -- that's the security boundary, execute() re-checks it regardless.
    But an undeclared tool is invisible to the model, so without this it either
    hallucinates an answer or goes vague. Naming it (not offering it) lets the
    model give the user an honest "you don't have permission for that" instead.
    """
    denied = denied_tools_for(principal)
    if not denied:
        return _SYSTEM_INSTRUCTION
    listing = "\n".join(f"- {d['name']}: {d['description']}" for d in denied)
    return (
        f"{_SYSTEM_INSTRUCTION}\n\n"
        "The following capabilities exist in this system but this user's role "
        f"does not have permission to use them:\n{listing}\n\n"
        "If the user's request needs one of these, tell them plainly they don't "
        "have permission for that -- do not attempt it, invent an answer, or "
        "pretend the capability doesn't exist."
    )


def _write_copilot_audit(
    principal: Principal,
    request_id: str,
    outcome: str,
    started: float,
    tools_called: list[dict],
) -> None:
    write_audit(
        hospital_id=principal.hospital_id,
        actor_id=principal.user_id,
        request_id=request_id,
        task_type="copilot",
        dedupe_key=request_id,  # a conversation turn is never replayed from cache
        prompt_version="v1",
        model_name=settings.gemini_model,
        outcome=outcome,
        latency_ms=int((time.monotonic() - started) * 1000),
        tools_called=tools_called,
    )


async def _run_turn(messages: list[ChatMessage], principal: Principal) -> AsyncIterator[str]:
    request_id = uuid.uuid4().hex
    started = time.monotonic()
    tools_called: list[dict] = []

    if not _ai_available():
        yield _sse("degraded", {
            "reason": "AI assistant is not configured. Deterministic search and "
                       "compliance lookups still work from the regular pages.",
        })
        yield _sse("done", {"request_id": request_id})
        return

    contents = [
        types.Content(role=m.role, parts=[types.Part.from_text(text=m.text)]) for m in messages
    ]
    declarations = declarations_for(principal)
    config = types.GenerateContentConfig(
        system_instruction=_system_instruction_for(principal),
        tools=(
            [types.Tool(function_declarations=[types.FunctionDeclaration(**d) for d in declarations])]
            if declarations
            else None
        ),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        http_options={"timeout": int(settings.llm_timeout_seconds * 1000)},
    )
    b = shared_breaker()

    for _round in range(_MAX_TOOL_ROUNDS):
        if not b.allow():
            yield _sse("degraded", {
                "reason": "AI assistant temporarily unavailable — too many recent failures, "
                          "retrying automatically shortly.",
            })
            _write_copilot_audit(principal, request_id, "breaker_open", started, tools_called)
            yield _sse("done", {"request_id": request_id})
            return

        text_parts: list[str] = []
        # The raw `Part`s, not `chunk.function_calls` -- that convenience
        # property strips each part down to a bare FunctionCall and drops
        # `thought_signature`, an opaque token Gemini 3 requires echoed back
        # on any function-call part sent in a later turn. Losing it is what
        # shipped as "AI assistant temporarily unavailable" on every real
        # tool round-trip: the next call 400s with "missing a
        # thought_signature in functionCall parts".
        function_call_parts: list = []
        try:
            # `.generate_content_stream(...)`'s own `AsyncIterator[...]` return
            # annotation describes what you get after awaiting it, not the
            # call itself -- the call returns a coroutine (confirmed against
            # the installed google-genai 2.18.0: `inspect.iscoroutine(...)`
            # is true before this `await`), and iterating a coroutine
            # directly raises TypeError, not a Gemini error.
            stream = await client().aio.models.generate_content_stream(
                model=settings.gemini_model, contents=contents, config=config
            )
            async for chunk in stream:
                if chunk.text:
                    text_parts.append(chunk.text)
                    yield _sse("delta", {"text": chunk.text})
                cand = chunk.candidates[0] if chunk.candidates else None
                for part in (cand.content.parts if cand and cand.content else None) or []:
                    if part.function_call is not None:
                        function_call_parts.append(part)
        except Exception as exc:  # noqa: BLE001 — any Gemini/network failure degrades this turn
            # A 429 is the provider asking this one call to back off, not
            # evidence of an outage -- ai/core.py's `_Retryable` split makes
            # the same exemption for ask_ai(); this path talks to Gemini
            # directly (streaming isn't cacheable) and needs it repeated
            # rather than inherited for free.
            status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            if status != 429:
                b.record(False)
            _log.exception("copilot stream failed request_id=%s", request_id)
            yield _sse("degraded", {
                "reason": "AI assistant temporarily unavailable — the last request to Gemini "
                          "failed. Check server logs for details.",
            })
            _write_copilot_audit(principal, request_id, "error", started, tools_called)
            yield _sse("done", {"request_id": request_id})
            return
        b.record(True)

        if not function_call_parts:
            _write_copilot_audit(principal, request_id, "live", started, tools_called)
            yield _sse("done", {"request_id": request_id})
            return

        contents.append(types.Content(role="model", parts=function_call_parts))
        response_parts = []
        for part in function_call_parts:
            fc = part.function_call
            name = fc.name or ""
            args = dict(fc.args or {})
            yield _sse("tool_start", {"name": name, "args": args})
            try:
                result = await execute(name, args, principal)
                tools_called.append({"name": name, "ok": True})
                yield _sse("tool_end", {"name": name, "ok": True})
            except ToolDenied as exc:
                # A forged or stale tool call from the model -- turned into a
                # function_response error, never a crash or a silent grant.
                result = {"error": str(exc)}
                tools_called.append({"name": name, "ok": False, "error": str(exc)})
                yield _sse("tool_end", {"name": name, "ok": False, "error": str(exc)})
            response_parts.append(types.Part.from_function_response(name=name, response=result))
        contents.append(types.Content(role="user", parts=response_parts))

    yield _sse("degraded", {"reason": "too many tool calls in one turn"})
    _write_copilot_audit(principal, request_id, "error", started, tools_called)
    yield _sse("done", {"request_id": request_id})


@copilot.post("/copilot/chat")
async def copilot_chat(
    body: ChatRequest,
    principal: Principal = Depends(require("copilot:chat")),
) -> StreamingResponse:
    return StreamingResponse(_run_turn(body.messages, principal), media_type="text/event-stream")
