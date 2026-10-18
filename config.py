"""Configuración local y de despliegue, sin exponer secretos en respuestas."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field


class Settings(BaseModel):
    firebase_project_id: str | None = None
    google_application_credentials: Path | None = Field(default=None, repr=False)
    firebase_check_revoked: bool = True
    firebase_use_emulators: bool = False
    firebase_timeout_seconds: float = Field(default=10, ge=1, le=60)
    firestore_database_id: str = Field(default="(default)", min_length=1)
    firestore_timeout_seconds: float = Field(default=10, ge=1, le=60)
    flashcards_backend: Literal["sqlite", "firestore"] = "sqlite"
    evaluations_backend: Literal["sqlite", "firestore"] = "sqlite"
    evaluations_db_path: Path = Path(__file__).parent / "data" / "evaluations.sqlite3"
    anthropic_api_key: str | None = Field(default=None, repr=False)
    anthropic_model: str = Field(default="claude-sonnet-4-6", min_length=1)
    anthropic_timeout_seconds: float = Field(default=30, ge=1, le=120)
    anthropic_max_retries: int = Field(default=1, ge=0, le=2)
    anthropic_max_tokens: int = Field(default=2048, ge=256, le=4096)
    flashcards_db_path: Path = Path(__file__).parent / "data" / "flashcards.sqlite3"
    fsrs_desired_retention: float = Field(default=0.9, ge=0.7, le=0.99)
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )


@lru_cache
def get_settings() -> Settings:
    load_dotenv(Path(__file__).parent / ".env", override=False)
    values = {}
    for field in Settings.model_fields:
        raw = os.getenv(field.upper())
        if raw is not None:
            values[field] = raw.strip()
    if "cors_origins" in values:
        values["cors_origins"] = [
            origin.strip() for origin in values["cors_origins"].split(",") if origin.strip()
        ]
    values["anthropic_api_key"] = values.get("anthropic_api_key") or None
    for name in ("firebase_project_id", "google_application_credentials"):
        values[name] = values.get(name) or None
    return Settings.model_validate(values)
