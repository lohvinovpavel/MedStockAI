"""LangGraph port: RBAC and zero-PHI properties
(docs/ai_workflows_migration_plan.md §3.2, §5.4, §8).

The migration plan's own parity suite is "the audit's 73 probes" -- this repo
has no recorded copy of them, so this file substitutes the two properties the
plan itself calls out as the ones that must not be lost: §3.2 ("the single
worst possible regression") and §5.4 ("the single most likely place for the
port to silently break zero-PHI"). No live GEMINI_API_KEY anywhere in this
file -- everything here is below the model, in the tool wrapper and the
tool_node's routing.
"""

import asyncio

import pytest
from app.graph import (
    CopilotState,
    RuntimeCtx,
    agent_node,
    as_langchain_tool,
    checked_execute,
    tool_node,
    tools_for,
)
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from medstock_shared.ai.tools import ToolDenied, all_specs, declarations_for
from medstock_shared.ai.tools.registry import ToolSpec
from medstock_shared.auth import PERMS, Principal
from medstock_shared.patient_assess import PatientAmbiguous
from pydantic import BaseModel, ValidationError

ROLES = list(PERMS)


def _principal(role: str) -> Principal:
    return Principal("user-1", "hospital-1", role)


@pytest.mark.parametrize("role", ROLES)
def test_tools_for_matches_declarations_for(role):
    """Phase 1 exit criterion: the LangChain-bound tool set is exactly the
    Gemini-declared tool set, for every role -- no drift between the two
    binding layers."""
    got = {t.name for t in tools_for(role)}
    want = {d["name"] for d in declarations_for(_principal(role))}
    assert got == want


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
def test_denied_tool_raises_even_when_bound_by_mistake(spec: ToolSpec):
    """§3.2's non-negotiable: the assert inside the wrapper is the boundary,
    not `bind_tools()`. A role that does not hold this tool's permission
    must get `ToolDenied` even called directly -- binding is a courtesy,
    never consulted here."""
    denied_role = next((r for r in ROLES if spec.permission not in PERMS[r]), None)
    if denied_role is None:
        pytest.skip(f"{spec.name} ({spec.permission}) is granted to every role")
    with pytest.raises(ToolDenied):
        checked_execute(spec, _principal(denied_role), {})


def test_granted_tool_is_not_denied():
    """Sanity check on the fixture above: a role that DOES hold the
    permission is not rejected by the same boundary. `find_drug_by_name`'s
    `name` field is required, so an empty call fails *arg validation*
    before ever reaching the DB -- `ValidationError`, specifically, not
    `ToolDenied` and not some other exception the DB might also raise."""
    spec = next(s for s in all_specs() if s.name == "find_drug_by_name")
    principal = _principal(next(r for r in ROLES if spec.permission in PERMS[r]))
    with pytest.raises(ValidationError):
        checked_execute(spec, principal, {})


class _AmbiguousArgs(BaseModel):
    patient_id: str = "smith"


def _raise_ambiguous(args, principal):
    raise PatientAmbiguous([
        {"full_name": "John Smith", "date_of_birth": "1970-01-01"},
        {"full_name": "Jane Smith", "date_of_birth": "1985-05-05"},
    ])


def _fake_ambiguous_tools() -> dict:
    spec = ToolSpec(
        name="resolve_patient_fake",
        permission="patient:read",
        description="test double",
        args=_AmbiguousArgs,
        fn=_raise_ambiguous,
    )
    return {"resolve_patient_fake": as_langchain_tool(spec)}


def _build_graph(agent, tools_by_name):
    async def fake_tools(state: CopilotState, writer):
        return await tool_node(state, writer, tools_by_name)

    graph = StateGraph(CopilotState, context_schema=RuntimeCtx)
    graph.add_node("agent", agent)
    graph.add_node("tools", fake_tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        lambda s: "tools" if getattr(s.messages[-1], "tool_calls", None) else END,
        {"tools": "tools", END: END},
    )
    return graph.compile()


def test_patient_ambiguous_never_enters_state_and_ends_the_turn():
    """§5.4: the candidate list (PHI) reaches the client only through the
    stream writer, never `messages` -- and the turn ends there, the model
    is never re-invoked. Exercises the real `tool_node`, wired to a fake
    tool that raises `PatientAmbiguous` instead of the real patient tools,
    so this needs no database."""
    tools_by_name = _fake_ambiguous_tools()
    agent_calls = 0

    async def fake_agent(state: CopilotState) -> dict:
        nonlocal agent_calls
        agent_calls += 1
        return {"messages": [AIMessage(content="", tool_calls=[
            {"name": "resolve_patient_fake", "args": {"patient_id": "smith"}, "id": "call_1"},
        ])]}

    compiled = _build_graph(fake_agent, tools_by_name)
    ctx = RuntimeCtx(principal=_principal("physician"), request_id="r1", system_instruction="")

    custom_events = []
    final_state = None

    async def run_and_capture():
        nonlocal final_state
        async for mode, payload in compiled.astream(
            {"messages": [{"role": "user", "content": "find John Smith"}]},
            context=ctx,
            stream_mode=["custom", "values"],
        ):
            if mode == "custom":
                custom_events.append(payload)
            else:
                final_state = payload

    asyncio.run(run_and_capture())

    assert agent_calls == 1, "the model must not be re-invoked after a disambiguation"
    assert any(e["event"] == "patient_disambiguation" for e in custom_events)
    disambiguation = next(e for e in custom_events if e["event"] == "patient_disambiguation")
    assert disambiguation["data"]["candidates"][0]["full_name"] == "John Smith"

    # The candidate list must never have entered checkpointed state: the
    # ambiguous call produced no ToolMessage at all (unlike a denied-tool
    # call, which does -- see the ToolDenied branch in tool_node), and no
    # *new* text mentioning the candidates was added beyond the user's own
    # typed input (that part is §5.7's separate, accepted case).
    assert not any(isinstance(m, ToolMessage) for m in final_state["messages"])
    contents_after_user_turn = [str(getattr(m, "content", "")) for m in final_state["messages"][1:]]
    assert not any("Smith" in c for c in contents_after_user_turn)


def test_prose_alongside_the_ambiguous_call_streams_exactly_like_legacy():
    """§5.1.2 says the disambiguation turn "produces zero delta events" --
    but that is an *empirical* property of Gemini (it does not preface a
    tool call with prose), not something either the legacy loop or this
    port enforce structurally: `copilot.py`'s `_run_turn` streams
    `chunk.text` the moment it arrives, mid-round, before it has seen
    whether that round's tool calls raise `PatientAmbiguous`. This test
    drives the real `agent_node` (a real model call, via
    `GenericFakeChatModel`, not a stub) to prove the graph reproduces that
    same (non-)guarantee -- text the model emits alongside an
    ambiguous-lookup tool call is visible on the "messages" stream channel
    exactly as `copilot.py` would emit it as `delta`, so `_run_turn_graph`
    does not need to (and must not) suppress it to match the legacy
    contract."""
    model = GenericFakeChatModel(
        messages=iter([
            AIMessage(
                content="Let me look that up.",
                tool_calls=[{"name": "resolve_patient_fake", "args": {"patient_id": "smith"}, "id": "call_1"}],
            ),
        ]),
        # GenericFakeChatModel's own `_stream()` only chunks `.content`, not
        # `.tool_calls` -- the fake, not real Gemini, would silently drop the
        # tool call under real streaming. Disabling its streaming still
        # exercises LangGraph's "messages" instrumentation around `.ainvoke`
        # (proven below), while keeping `tool_calls` intact.
        disable_streaming=True,
    )
    tools_by_name = _fake_ambiguous_tools()

    async def real_agent(state: CopilotState) -> dict:
        return await agent_node(state, model)

    compiled = _build_graph(real_agent, tools_by_name)
    ctx = RuntimeCtx(principal=_principal("physician"), request_id="r1", system_instruction="")

    events = []

    async def run():
        async for mode, payload in compiled.astream(
            {"messages": [{"role": "user", "content": "find John Smith"}]},
            context=ctx,
            stream_mode=["custom", "messages"],
        ):
            events.append((mode, payload))

    asyncio.run(run())

    streamed_text = "".join(
        chunk.content for mode, (chunk, meta) in events
        if mode == "messages" and isinstance(chunk.content, str)
    )
    # Same (non-)guarantee as legacy: prose the model attaches to a tool
    # call round IS streamed before the round's outcome is known. This is
    # not new exposure -- `_run_turn`'s `yield _sse("delta", ...)` runs
    # inside the same streaming loop, before `execute()` is ever called.
    assert streamed_text == "Let me look that up."
    assert any(p["event"] == "patient_disambiguation" for mode, p in events if mode == "custom")
