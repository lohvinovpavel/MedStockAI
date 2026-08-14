import sys
from pathlib import Path

# Each service ships an `app` package. Put this service first on sys.path so
# `from app.main import app` is compliance, not whichever workspace member
# uv installed last.
sys.path.insert(0, str(Path(__file__).resolve().parent))
