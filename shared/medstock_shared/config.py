from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
