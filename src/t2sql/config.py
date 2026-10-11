"""Settings, read from environment variables (prefix T2SQL_) or a .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="T2SQL_", extra="ignore")

    # SQLite works out of the box; for PostgreSQL use
    # postgresql+psycopg://user:password@localhost:5432/analytics
    database_url: str = "sqlite:///data/analytics.db"

    # "offline" uses the rule-based semantic-layer planner (no API key).
    llm_provider: Literal["offline", "openai", "anthropic"] = "offline"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    anthropic_model: str = "claude-sonnet-4-5"

    # Conversation memory for follow-up questions.
    memory_backend: Literal["memory", "mongo"] = "memory"
    mongo_uri: str | None = None
    mongo_db: str = "t2sql"
    mongo_collection: str = "sessions"
    history_turns: int = 5

    # Guardrails
    max_rows: int = 200
    max_attempts: int = 3            # SQL generation attempts (first try + self-corrections)
    statement_timeout_ms: int = 5000

    api_key: str | None = None       # if set, API requests need header X-API-Key


@lru_cache
def get_settings() -> Settings:
    return Settings()
