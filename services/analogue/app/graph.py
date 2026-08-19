"""LangGraph port of the copilot's tool-calling loop
(docs/ai_workflows_migration_plan.md Phase 1 + Phase 2).

Phase 1 -- `as_langchain_tool()` / `tools_for()`: a LangChain wrapper over the
existing `medstock_shared.ai.tools` registry. `llm.bind_tools(...)` on these
is the analogue of `declarations_for()` -- it decides what the model is
*told about*, and is not the security boundary. The permission assert inside
`_run` is the boundary, mirroring `registry.execute()`'s own re-check so the
property holds even if a tool is bound by mistake (§3.2's non-negotiable).

Phase 2 -- one ReAct graph (`agent` <-> `tools`) replacing the manual
`for _round in range(_MAX_TOOL_ROUNDS)` loop in `copilot.py`. Same system
prompt, same tools, same SSE contract -- `copilot.py` still owns translating
graph events into `delta` / `tool_start` / `tool_end` / `tool_card` /
`patient_disambiguation` / `degraded` / `done` frames, this module only
produces the events to translate.

Not yet ported (later phases, each independently shippable per §7):
supervisor + specialist subgraphs (Phase 4), the verifier node (§4.3),
durable `PostgresSaver` + HITL `interrupt()` (Phase 5), LangTrace (Phase 3).
`InMemorySaver` below is exactly what Phase 2's exit criteria calls for --
resumability within a process, no new durable PHI surface.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from langchain_core.messages import AnyMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import get_runtime
from langgraph.types import Command, StreamWriter
from medstock_shared.ai import shared_breaker
from medstock_shared.ai.cards import card_for
from medstock_shared.ai.tools import ToolDenied, all_specs
from medstock_shared.ai.tools.registry import ToolSpec
from medstock_shared.auth import PERMS, Principal
from medstock_shared.config import settings
from medstock_shared.patient_assess import PatientAmbiguous
from pydantic import BaseModel, ConfigDict

_log = logging.getLogger(__name__)


class BreakerOpen(Exception):
    """The shared circuit breaker is open -- raised from `agent_node` instead
    of calling Gemini, so `copilot.py` can tell this apart from a live
    failure (same distinction `copilot.py`'s legacy loop makes today)."""


# --------------------------------------------------------------------------
# Phase 1 -- tool wrapper (§3.2, §3.3)
# --------------------------------------------------------------------------


@dataclass
class RuntimeCtx:
    """Request-scoped authority, kept out of `CopilotState` on purpose (§3.2):
    state is checkpointed, `Principal` is not something that belongs in a
    durable store. Tools read it via `get_runtime`, not from graph state."""

    principal: Principal
    request_id: str
    system_instruction: str


def checked_execute(spec: ToolSpec, principal: Principal, kwargs: dict) -> dict:
    """The boundary itself, factored out of `as_langchain_tool` so it is
    directly unit-testable without going through LangChain's own input
    validation (tests/test_graph_rbac.py). NOT a formality: binding
    (`tools_for`, below) is the courtesy layer that decides what the model
    is offered -- this must hold even if a denied tool is bound by
    mistake, the property `registry.execute()` already gives us."""
    if spec.permission not in PERMS.get(principal.role, set()):
        raise ToolDenied(f"{spec.name} requires {spec.permission}")
    args = spec.args.model_validate(kwargs)
    return spec.fn(args, principal)


def as_langchain_tool(spec: ToolSpec) -> BaseTool:
    def _run(**kwargs) -> dict:
        principal = get_runtime(RuntimeCtx).context.principal
        return checked_execute(spec, principal, kwargs)

    return StructuredTool.from_function(
        func=_run, name=spec.name, description=spec.description, args_schema=spec.args
    )


def tools_for(role: str) -> list[BaseTool]:
    """Every tool this role may call, wrapped for `llm.bind_tools()`. Cheap
    (no I/O) -- `build_graph` below caches the compiled graph per role so
    this only runs once per role for the life of the process."""
    granted = PERMS.get(role, set())
    return [as_langchain_tool(s) for s in all_specs() if s.permission in granted]


# --------------------------------------------------------------------------
# Phase 2 -- graph state and nodes (§2.1, §3.1 trimmed, §4.1)
# --------------------------------------------------------------------------


class CopilotState(BaseModel):
    """Checkpointed state. Specialist routing, cards, HITL and the verifier
    are later phases -- adding those fields now, unused, is exactly the
    speculative state `extra="forbid"` exists to catch (§3.1)."""

    messages: Annotated[list[AnyMessage], add_messages]

    model_config = ConfigDict(extra="forbid")


async def agent_node(state: CopilotState, model: ChatGoogleGenerativeAI) -> dict:
    rt = get_runtime(RuntimeCtx)
    breaker = shared_breaker()
    if not breaker.allow():
        raise BreakerOpen
    try:
        ai = await model.ainvoke([SystemMessage(content=rt.context.system_instruction), *state.messages])
    except Exception as exc:
        # A 429 is the provider asking this call to back off, not evidence of
        # an outage -- same exemption copilot.py's legacy loop makes and
        # ask_ai()'s own `_Retryable` split makes for cached calls (§6).
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if status != 429:
            breaker.record(False)
        raise
    breaker.record(True)
    return {"messages": [ai]}


def extract_text_delta(content) -> str:
    """A streamed `AIMessageChunk.content` is a plain string for ordinary text
    but a list of typed content blocks when the model emits anything else
    alongside it (thinking, tool-use fragments) -- pull out only the `text`
    blocks, same as `copilot.py`'s `chunk.text` read from the raw SDK today."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


async def tool_node(state: CopilotState, writer: StreamWriter, tools_by_name: dict[str, BaseTool]) -> Command:
    """Custom, not `langgraph.prebuilt.ToolNode`: a prebuilt node turns every
    tool exception into `ToolMessage` content, which would put
    `PatientAmbiguous`'s candidate list into `messages` -- straight into
    model context and (from Phase 5) the checkpointer. That is §5.4's PHI
    leak arriving two phases early. This node emits the candidate list on
    the stream channel only and ends the turn, exactly like `copilot.py`'s
    legacy loop does today."""
    rt = get_runtime(RuntimeCtx)
    principal, request_id = rt.context.principal, rt.context.request_id
    last = state.messages[-1]
    out_messages: list[ToolMessage] = []
    for tc in last.tool_calls:
        name, args, call_id = tc["name"], tc["args"], tc["id"]
        writer({"event": "tool_start", "data": {"name": name, "args": args}})
        tool = tools_by_name.get(name)
        try:
            if tool is None:
                raise ToolDenied(f"no such tool: {name!r}")
            result = await tool.ainvoke(args)
        except ToolDenied as exc:
            result = {"error": str(exc)}
            writer({"event": "tool_end", "data": {"name": name, "ok": False, "error": str(exc)}})
            out_messages.append(ToolMessage(content=json.dumps(result), tool_call_id=call_id, name=name))
            continue
        except PatientAmbiguous as exc:
            # PHI leaves through the stream channel only -- never a
            # ToolMessage, never state, never a Command update.
            writer({"event": "tool_end", "data": {"name": name, "ok": False, "error": "ambiguous patient name"}})
            writer({"event": "patient_disambiguation", "data": {
                "tool": name, "query": str(args.get("patient_id") or ""), "candidates": exc.candidates,
            }})
            return Command(goto=END, update={})
        writer({"event": "tool_end", "data": {"name": name, "ok": True}})
        card_data = card_for(name, result, principal, request_id, args=args)
        if card_data is not None:
            writer({"event": "tool_card", "data": {"name": name, "card": card_data}})
        out_messages.append(ToolMessage(content=json.dumps(result), tool_call_id=call_id, name=name))
    return Command(goto="agent", update={"messages": out_messages})


def _route(state: CopilotState) -> str:
    last = state.messages[-1]
    return "tools" if getattr(last, "tool_calls", None) else END


@lru_cache(maxsize=8)  # one compiled graph per role -- four roles, ever (§3.3)
def build_graph(role: str):
    bound_tools = tools_for(role)
    tools_by_name = {t.name: t for t in bound_tools}
    model = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        timeout=settings.llm_timeout_seconds,
        max_retries=0,  # don't let LangChain's own retries mask an outage from the breaker (§6)
    ).bind_tools(bound_tools)

    # `async def` closures, not lambdas: LangGraph dispatches sync vs async
    # nodes by `inspect.iscoroutinefunction`, which a lambda returning a
    # coroutine does not satisfy -- it would get invoked as a sync node and
    # hand back an un-awaited coroutine as the node's output.
    async def _agent(state: CopilotState) -> dict:
        return await agent_node(state, model)

    async def _tools(state: CopilotState, writer: StreamWriter) -> Command:
        return await tool_node(state, writer, tools_by_name)

    graph = StateGraph(CopilotState, context_schema=RuntimeCtx)
    graph.add_node("agent", _agent)
    graph.add_node("tools", _tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _route, {"tools": "tools", END: END})
    # "tools" routes onward via the Command it returns, not a static edge --
    # a static "tools" -> "agent" edge would fire *in addition* to the
    # Command and re-invoke the model after a disambiguation, exactly what
    # §5.1.2 requires never happens.
    return graph.compile(checkpointer=InMemorySaver())
