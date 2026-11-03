"""Application configuration from ``MMA_*`` environment variables (or a git-ignored ``.env``).

Nothing secret has a default: the app refuses to start without ``MMA_JWT_SECRET``.
The Anthropic key is read by the SDK from ``ANTHROPIC_API_KEY``; without it the assistant runs
entirely on local models (speech recognition, speech synthesis, OCR, embeddings).
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MMA_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://mma:mma@localhost:5432/mma"

    # --- security ---
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    login_rate_limit_per_minute: int = 10
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # --- files ---
    file_dir: Path = Path("./var/files")
    max_upload_mb: int = 25
    max_pdf_pages: int = 100
    max_audio_seconds: int = 600

    # --- models (all local except the optional Claude provider) ---
    llm_provider: Literal["auto", "claude", "local"] = "auto"
    claude_model: str = "claude-opus-5-5"
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    claude_fallbacks: bool = True
    stt_model: str = "base.en"  # faster-whisper size: tiny.en | base.en | small.en ...
    stt_compute_type: str = "int8"
    tts_voice: str = "en_US-ljspeech-medium"  # Piper voice
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    model_cache_dir: str | None = None  # where downloaded model files are kept
    model_threads: int | None = None

    # --- assistant ---
    retrieval_top_k: int = 5
    # cross-encoder score below which the files "don't answer" a question (calibrated by the evaluation)
    answer_threshold: float = -3.0

    # --- logging ---
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("jwt_secret")
    @classmethod
    def _secret_strength(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("MMA_JWT_SECRET must be at least 32 characters")
        return v

    @property
    def llm_mode(self) -> Literal["claude", "local"]:
        """Resolve ``auto``: Claude when an API key is configured, otherwise local."""
        if self.llm_provider == "auto":
            return "claude" if os.environ.get("ANTHROPIC_API_KEY") else "local"
        return self.llm_provider


@lru_cache
def get_settings() -> Settings:
    return Settings()
