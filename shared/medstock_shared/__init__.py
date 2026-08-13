from .auth import COOKIE_NAME, Principal, current_principal, require
from .config import Settings, settings
from .db import engine, session_scope
from .models import AICache, AppUser, Base, Hospital, Membership

__all__ = [
    "AICache", "AIError", "AITask", "AppUser", "Base", "COOKIE_NAME", "Hospital",
    "Membership", "Principal", "Settings", "TASKS", "ask_ai", "current_principal",
    "dedupe_key", "engine", "require", "session_scope", "settings",
]


def __getattr__(name: str):
    """Load Gemini helpers only when asked. Importing auth/db for UC-1
    must not construct a genai.Client (empty GEMINI_API_KEY raises)."""
    if name in {"AIError", "ask_ai", "dedupe_key"}:
        from .ai import AIError, ask_ai, dedupe_key

        return {"AIError": AIError, "ask_ai": ask_ai, "dedupe_key": dedupe_key}[name]
    if name in {"TASKS", "AITask"}:
        from .ai_tasks import TASKS, AITask

        return {"TASKS": TASKS, "AITask": AITask}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
