"""Casos de uso de flashcards, separados del almacenamiento y del generador."""

import logging
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable
from uuid import UUID

from fastapi import HTTPException

from config import get_settings
from models.flashcards import DraftCard, DueFlashcards, Flashcard, FlashcardsBatch, ReviewFlashcardRequest, ReviewResult
from services.flashcard_repository import FlashcardRepository, SQLiteFlashcardRepository
from services.fsrs_engine import FsrsEngine, utc_datetime
from services.firebase import FirebaseConfigurationError, get_firestore_client
from services.firestore_repository import FirestoreFlashcardRepository
from google.auth.exceptions import GoogleAuthError
from google.api_core.exceptions import GoogleAPICallError


logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FlashcardsService:
    def __init__(
        self, repository: FlashcardRepository, engine: FsrsEngine,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.repository = repository
        self.engine = engine
        self.clock = clock

    def create(self, user_id: str, drafts: list[DraftCard]) -> FlashcardsBatch:
        return self.repository.create(user_id, drafts, utc_datetime(self.clock()), self.engine)

    def due(self, user_id: str, limit: int) -> DueFlashcards:
        at = utc_datetime(self.clock())
        return DueFlashcards(user_id=user_id, as_of=at, cards=self.repository.due(user_id, at, limit))

    def get(self, user_id: str, card_id: UUID) -> Flashcard:
        return self.repository.get(user_id, card_id)

    def review(self, request: ReviewFlashcardRequest) -> ReviewResult:
        return self.repository.review(request, utc_datetime(self.clock()), self.engine)


@lru_cache
def get_flashcards_service() -> FlashcardsService:
    settings = get_settings()
    path = settings.flashcards_db_path
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    try:
        if settings.flashcards_backend == "firestore":
            return FlashcardsService(
                FirestoreFlashcardRepository(get_firestore_client(), settings.firestore_timeout_seconds),
                FsrsEngine(settings.fsrs_desired_retention),
            )
        return FlashcardsService(
            SQLiteFlashcardRepository(path), FsrsEngine(settings.fsrs_desired_retention)
        )
    except (OSError, sqlite3.DatabaseError, FirebaseConfigurationError, GoogleAuthError, GoogleAPICallError) as exc:
        logger.warning("Almacenamiento de flashcards no disponible (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="El almacenamiento de flashcards no está disponible") from exc
