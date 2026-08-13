import sys
from pathlib import Path

import pytest

# Each service ships an `app` package. Put this service first on sys.path so
# `from app.main import app` is analogue, not whichever workspace member
# uv installed last.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from medstock_shared.config import settings


@pytest.fixture(autouse=True)
def _gemini_configured(monkeypatch):
    """Existing UC-5 tests assume a key so omitted use_ai still defaults true."""
    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini-key")
