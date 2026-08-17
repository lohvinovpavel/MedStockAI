from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_files() -> tuple[str, ...]:
    """Load repo-root .env even when CWD is a service directory (uvicorn).

    pydantic's env_file='.env' is CWD-relative. Analogue runs from
    services/analogue, so a key in the repo-root .env was previously ignored
    unless GEMINI_API_KEY was already in the process environment.
    Later files override earlier ones; process env still wins.
    """
    candidates = [
        Path(__file__).resolve().parents[2] / ".env",
        Path(".env"),
    ]
    seen: set[Path] = set()
    files: list[str] = []
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_file() or resolved in seen:
            continue
        seen.add(resolved)
        files.append(str(resolved))
    return tuple(files)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_env_files(), extra="ignore")

    database_url: str = "postgresql+psycopg://medstock:medstock@localhost:5432/medstock"
    jwt_public_key: str = ""
    jwt_algorithm: str = "RS256"
    # auth only — the private key never leaves that service. The other six
    # hold jwt_public_key and nothing else.
    jwt_private_key: str = ""
    jwt_ttl_hours: int = 8

    # ask_ai() only — set on analogue and prediction, unused elsewhere
    gemini_api_key: str = ""
    # Single source for the Gemini model id. Override with env GEMINI_MODEL.
    gemini_model: str = "gemini-3.5-flash"
    llm_timeout_seconds: float = 20.0


settings = Settings()
