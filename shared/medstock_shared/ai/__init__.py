"""In-process AI module: Gemini calls, retry, circuit breaker, cache.

This package replaces the old single-file `medstock_shared/ai.py` in place --
`from medstock_shared import ask_ai, AIError` and `from medstock_shared.ai
import ask_ai` both still resolve here unchanged. See
docs/ai-module-plan.md Phase 1.
"""

from .breaker import CircuitBreaker, CircuitBreakerConfig
from .cache import cache_get, cache_put
from .core import AIError, ask_ai, dedupe_key

__all__ = [
    "AIError",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "ask_ai",
    "cache_get",
    "cache_put",
    "dedupe_key",
]
