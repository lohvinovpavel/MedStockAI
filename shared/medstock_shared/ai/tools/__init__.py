"""Copilot tool registry. Importing this package is what populates it --
`pharmacy` registers its tools as a side effect of being imported here, the
same way `ai_tasks.TASKS` is populated by module-level dict literals."""

from . import pharmacy  # noqa: F401  -- import for its @tool registration side effect
from .registry import (
    ToolDenied,
    ToolSpec,
    all_specs,
    declarations_for,
    denied_tools_for,
    execute,
    tool,
)

__all__ = [
    "ToolDenied",
    "ToolSpec",
    "all_specs",
    "declarations_for",
    "denied_tools_for",
    "execute",
    "tool",
]
