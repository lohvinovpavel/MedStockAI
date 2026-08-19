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

`settings.copilot_graph_enabled` switches the whole route to `_run_turn_graph`
(`.graph`, docs/ai_workflows_migration_plan.md Phase 2) instead of the
`_run_turn` loop below. Same SSE contract either way -- see `.graph`'s module
docstring for what's ported and what isn't yet.
"""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from google.genai import types
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError
from medstock_shared.ai import client, shared_breaker, write_audit
from medstock_shared.ai.cards import card_for
from medstock_shared.ai.tools import ToolDenied, declarations_for, denied_tools_for, execute
from medstock_shared.auth import Principal, require
from medstock_shared.config import settings
from medstock_shared.patient_assess import PatientAmbiguous
from pydantic import BaseModel

from .graph import BreakerOpen, RuntimeCtx, build_graph, extract_text_delta

copilot = APIRouter()
_log = logging.getLogger("analogue.copilot")

# One user turn can chain several tool calls (search, then verify each hit);
# this bounds the loop so a model that never stops calling tools can't hold
# the connection open forever.
_MAX_TOOL_ROUNDS = 6

# LangGraph path (docs/ai_workflows_migration_plan.md §4.1): a recursion
# limit bounds agent<->tools supersteps, not tool-call rounds directly, so
# it is set roughly double _MAX_TOOL_ROUNDS to bound to the same real number
# of tool rounds (each round is one "agent" + one "tools" superstep).
_GRAPH_RECURSION_LIMIT = 12

# All four roles share this one endpoint (PERMS in medstock_shared.auth), and
# a physician asking "can I prescribe X" is not a pharmacist -- a prompt that
# hardcoded "for a hospital pharmacist" regardless of caller measurably made
# the model decline questions the role's own tools (assess_patient_for_drug,
# get_patient_regimen) exist to answer, treating an informational safety
# check as if it were an authorization the model itself isn't allowed to give.
_ROLE_TITLES = {
    "pharmacist": "a hospital pharmacist",
    "physician": "a hospital physician",
    "director": "a clinical director",
    "admin": "a procurement officer",
}


def _system_instruction_base(role: str) -> str:
    title = _ROLE_TITLES.get(role, "a hospital staff member")
    return (
        f"You are the MedStock AI assistant for {title}. Answer only from your "
        "tools' results and what the user tells you -- never invent an RxCUI, "
        "NDC, stock number, or certification status. If a tool returns no "
        "usable result, say so plainly rather than guessing. Calling a "
        "read-only or assessment tool is not the same as authorizing, "
        "prescribing, or committing anything -- use the tools you are given "
        "whenever they answer the question.\n\n"
        "Never propose ordering, reordering or sourcing a drug without stating its current certification "
        "status. If a tool reports a compliance block, say so plainly and do not offer a workaround.\n\n"
        "If the user names a drug without an identifier, call find_drug_by_name before any tool that "
        "takes an rxcui or ndc. If you do not have an identifier from a tool result, say so — do not "
        "supply one from your own knowledge.\n\n"
        "A tool that returns an empty list may mean 'nothing to report' or 'nothing was measured'. Check "
        "the coverage fields in the result. If nothing was measured, say that — never present an absence "
        "of data as a clean or safe result, and never give an operational go-ahead on that basis.\n\n"
        "If the user asks for a metric this system does not track, say plainly that it is not tracked "
        "before offering anything else. Never label a figure with a metric name the user supplied unless "
        "it is that exact metric. Name every figure by what the tool calls it — query_ai_decisions "
        "reports AI tool-call outcomes, not clinical or medication error rates.\n\n"
        "If you cannot satisfy part of the request with the tools available, say which part and why, in "
        "the same reply as the part you could answer."
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
    base = _system_instruction_base(principal.role)
    denied = denied_tools_for(principal)
    if not denied:
        return base
    listing = "\n".join(f"- {d['name']}: {d['description']}" for d in denied)
    return (
        f"{base}\n\n"
        "The following capabilities exist in this system but this user's role "
        f"does not have permission to use them:\n{listing}\n\n"
        "Only the tools listed above are unavailable to this user. Every other tool you can call is "
        "permitted — if a tool call succeeds, use its result. Never tell the user they lack permission "
        "for a tool you were able to call.\n\n"
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
    successful_tools: set[str] = set()

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
                successful_tools.add(name)
                yield _sse("tool_end", {"name": name, "ok": True})
                card_data = card_for(name, result, principal, request_id, args=args)
                if card_data is not None:
                    yield _sse("tool_card", {"name": name, "card": card_data})
            except ToolDenied as exc:
                # A forged or stale tool call from the model -- turned into a
                # function_response error, never a crash or a silent grant.
                result = {"error": str(exc)}
                tools_called.append({"name": name, "ok": False, "error": str(exc)})
                yield _sse("tool_end", {"name": name, "ok": False, "error": str(exc)})
            except PatientAmbiguous as exc:
                # A name matched more than one patient. The candidate list is
                # PHI (name + DOB) -- it goes straight to the frontend's own
                # picker, never into a function_response Gemini would read, and
                # the turn ends here rather than continuing the round loop.
                yield _sse("tool_end", {"name": name, "ok": False, "error": "ambiguous patient name"})
                yield _sse("patient_disambiguation", {
                    "tool": name,
                    "query": str(args.get("patient_id") or ""),
                    "candidates": exc.candidates,
                })
                tools_called.append({"name": name, "ok": False, "error": "ambiguous patient name"})
                _write_copilot_audit(principal, request_id, "disambiguation", started, tools_called)
                yield _sse("done", {"request_id": request_id})
                return
            response_parts.append(types.Part.from_function_response(name=name, response=result))
        contents.append(types.Content(role="user", parts=response_parts))

    yield _sse("degraded", {"reason": "too many tool calls in one turn"})
    _write_copilot_audit(principal, request_id, "error", started, tools_called)
    yield _sse("done", {"request_id": request_id})


async def _run_turn_graph(messages: list[ChatMessage], principal: Principal) -> AsyncIterator[str]:
    """LangGraph path (docs/ai_workflows_migration_plan.md Phase 2), gated by
    `settings.copilot_graph_enabled`. Same SSE contract as `_run_turn` above
    -- this function only differs in how it produces the frames, not which
    frames it produces or what they carry, per §4.4 and §7 Phase 2's exit
    criteria (byte-compatible frames)."""
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

    lc_messages = [
        AIMessage(content=m.text) if m.role == "model" else HumanMessage(content=m.text) for m in messages
    ]
    ctx = RuntimeCtx(
        principal=principal,
        request_id=request_id,
        system_instruction=_system_instruction_for(principal),
    )
    graph = build_graph(principal.role)
    config = {"configurable": {"thread_id": request_id}, "recursion_limit": _GRAPH_RECURSION_LIMIT}

    outcome = "live"
    try:
        async for mode, payload in graph.astream(
            {"messages": lc_messages}, context=ctx, config=config, stream_mode=["messages", "custom"]
        ):
            if mode == "messages":
                chunk, meta = payload
                # Only the "agent" node's tokens reach the user (§4.4) -- in
                # Phase 2 that is every node that talks to the model, since
                # the supervisor/specialist split is Phase 4.
                if meta.get("langgraph_node") != "agent":
                    continue
                text = extract_text_delta(chunk.content)
                if text:
                    yield _sse("delta", {"text": text})
                continue

            event, data = payload["event"], payload["data"]
            if event == "tool_end":
                tools_called.append({"name": data["name"], "ok": data["ok"], **(
                    {"error": data["error"]} if "error" in data else {}
                )})
            elif event == "patient_disambiguation":
                outcome = "disambiguation"
            yield _sse(event, data)
            if event == "patient_disambiguation":
                # Same short-circuit as the legacy loop's PatientAmbiguous
                # branch: the turn ends here, the model is never re-invoked.
                _write_copilot_audit(principal, request_id, outcome, started, tools_called)
                yield _sse("done", {"request_id": request_id})
                return
    except BreakerOpen:
        yield _sse("degraded", {
            "reason": "AI assistant temporarily unavailable — too many recent failures, "
                      "retrying automatically shortly.",
        })
        _write_copilot_audit(principal, request_id, "breaker_open", started, tools_called)
        yield _sse("done", {"request_id": request_id})
        return
    except GraphRecursionError:
        yield _sse("degraded", {"reason": "too many tool calls in one turn"})
        _write_copilot_audit(principal, request_id, "error", started, tools_called)
        yield _sse("done", {"request_id": request_id})
        return
    except Exception:  # noqa: BLE001 -- any Gemini/network failure degrades this turn, same as _run_turn
        _log.exception("copilot graph turn failed request_id=%s", request_id)
        yield _sse("degraded", {
            "reason": "AI assistant temporarily unavailable — the last request to Gemini "
                      "failed. Check server logs for details.",
        })
        _write_copilot_audit(principal, request_id, "error", started, tools_called)
        yield _sse("done", {"request_id": request_id})
        return

    _write_copilot_audit(principal, request_id, outcome, started, tools_called)
    yield _sse("done", {"request_id": request_id})


@copilot.post("/copilot/chat")
@copilot.post("/chat")
async def copilot_chat(
    body: ChatRequest,
    principal: Principal = Depends(require("copilot:chat")),
) -> StreamingResponse:
    turn = _run_turn_graph if settings.copilot_graph_enabled else _run_turn
    return StreamingResponse(turn(body.messages, principal), media_type="text/event-stream")
