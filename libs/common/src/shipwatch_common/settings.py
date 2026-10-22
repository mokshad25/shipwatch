from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Just what database tooling (Alembic, scripts) needs, so it runs without service secrets."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://shipwatch:shipwatch@localhost:5432/shipwatch"


class Settings(DatabaseSettings):
    """Process configuration, read from environment variables (and `.env` if present)."""

    env: str = "dev"
    log_level: str = "INFO"
    github_webhook_secret: SecretStr


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
