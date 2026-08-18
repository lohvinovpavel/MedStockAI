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

Never a write-approval tool here (docs/ai-module-plan.md Phase 4): nothing
in `medstock_shared.ai.tools` sends a PO or approves anything. A copilot that
reads drug-label text it was asked to summarise and can also approve
purchases is a copilot that can be prompt-injected into approving purchases.
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
from medstock_shared.ai.tools import ToolDenied, declarations_for, execute
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
        system_instruction=_SYSTEM_INSTRUCTION,
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
            yield _sse("degraded", {"reason": "AI assistant temporarily unavailable"})
            _write_copilot_audit(principal, request_id, "breaker_open", started, tools_called)
            yield _sse("done", {"request_id": request_id})
            return

        text_parts: list[str] = []
        function_calls: list = []
        try:
            async for chunk in client().aio.models.generate_content_stream(
                model=settings.gemini_model, contents=contents, config=config
            ):
                if chunk.text:
                    text_parts.append(chunk.text)
                    yield _sse("delta", {"text": chunk.text})
                if chunk.function_calls:
                    function_calls.extend(chunk.function_calls)
        except Exception:  # noqa: BLE001 — any Gemini/network failure degrades this turn
            b.record(False)
            _log.exception("copilot stream failed request_id=%s", request_id)
            yield _sse("degraded", {"reason": "AI assistant temporarily unavailable"})
            _write_copilot_audit(principal, request_id, "error", started, tools_called)
            yield _sse("done", {"request_id": request_id})
            return
        b.record(True)

        if not function_calls:
            _write_copilot_audit(principal, request_id, "live", started, tools_called)
            yield _sse("done", {"request_id": request_id})
            return

        contents.append(
            types.Content(role="model", parts=[types.Part(function_call=fc) for fc in function_calls])
        )
        response_parts = []
        for fc in function_calls:
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
