"""Tool registry for the copilot (docs/ai-module-plan.md §3/Phase 4).

A tool binds to a `medstock_shared.auth` permission, not a role directly --
`auth.PERMS` is already the single source of the role -> capability map, with
its own reasoning for what each permission is allowed to touch. Binding a
tool to a permission means it inherits that reasoning for free instead of
this module keeping a second, parallel ACL that could drift from the first.

Declaring a tool to Gemini via `declarations_for()` is a courtesy to the
model, not the security boundary -- a model can hallucinate a tool name it
was never offered. `execute()` re-checks the same permission against the
caller's actual `Principal` before running anything, every time.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from ...auth import PERMS, Principal

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    permission: str
    description: str
    args: type[BaseModel]
    # Plain sync function, like every other DB-touching call in this repo
    # (services.md's own pattern) -- `execute()` runs it in a threadpool so
    # the copilot's async route never blocks its event loop on one.
    fn: Callable[[BaseModel, Principal], dict]

    def to_declaration(self) -> dict:
        """Gemini FunctionDeclaration kwargs. Pydantic's own JSON Schema is
        passed straight through as `parameters_json_schema` -- no hand
        translation to Gemini's schema dialect to keep in sync by hand."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters_json_schema": self.args.model_json_schema(),
        }


_REGISTRY: dict[str, ToolSpec] = {}


def tool(*, permission: str, description: str, args: type[BaseModel]) -> Callable:
    """Decorator: registers `fn(args, principal) -> dict` under `fn.__name__`."""

    def decorator(fn: Callable[[BaseModel, Principal], dict]) -> Callable:
        _REGISTRY[fn.__name__] = ToolSpec(
            name=fn.__name__,
            permission=permission,
            description=description,
            args=args,
            fn=fn,
        )
        return fn

    return decorator


def declarations_for(principal: Principal) -> list[dict]:
    """Gemini function declarations for exactly the tools this role may call."""
    granted = PERMS.get(principal.role, set())
    return [spec.to_declaration() for spec in _REGISTRY.values() if spec.permission in granted]


def denied_tools_for(principal: Principal) -> list[dict]:
    """The complement of declarations_for(): name + description for tools this
    role may not call. Not declared to Gemini as callable -- folded into the
    system prompt instead, so a role-gated request gets an explicit "you
    don't have permission" instead of the model either hallucinating an
    answer or just going quiet about a capability it was never told exists."""
    granted = PERMS.get(principal.role, set())
    return [
        {"name": spec.name, "description": spec.description}
        for spec in _REGISTRY.values()
        if spec.permission not in granted
    ]


class ToolDenied(Exception):
    """Raised by `execute()` -- an unknown name (a stale/forged tool call) or
    a permission the caller's role does not hold. Both are turned into a
    function_response error back to the model, never a crash."""


async def execute(name: str, raw_args: dict, principal: Principal) -> dict:
    """Look up, re-authorize, validate, and run one tool call."""
    spec = _REGISTRY.get(name)
    if spec is None:
        raise ToolDenied(f"no such tool: {name!r}")
    if spec.permission not in PERMS.get(principal.role, set()):
        _log.warning(
            "tool %r called by role %r without permission %r", name, principal.role, spec.permission
        )
        raise ToolDenied(f"role {principal.role!r} may not call {name!r}")
    args = spec.args.model_validate(raw_args)
    return await run_in_threadpool(spec.fn, args, principal)
