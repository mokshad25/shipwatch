from functools import lru_cache

from pydantic import SecretStr, field_validator
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
    max_body_bytes: int = 5 * 1024 * 1024

    @field_validator("github_webhook_secret")
    @classmethod
    def _secret_is_strong(cls, value: SecretStr) -> SecretStr:
        # An empty or placeholder secret makes signatures forgeable; refuse to start instead.
        secret = value.get_secret_value()
        if secret == "change-me" or len(secret.strip()) < 16:
            raise ValueError(
                "GITHUB_WEBHOOK_SECRET must be a real secret of at least 16 characters"
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
