"""Casos de uso para sesiones inmutables y selección explícita del almacenamiento."""

import logging
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError

from config import get_settings
from models.evaluations import EvaluationInput, EvaluationSession, EvaluationResult
from services.evaluation_repository import EvaluationRepository, EvaluationStorageError, SessionNotFoundError, SQLiteEvaluationRepository, require_same_submission
from services.firebase import FirebaseConfigurationError, get_firestore_client
from services.firestore_evaluations import FirestoreEvaluationRepository


logger = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc)


class EvaluationsService:
    def __init__(self, repository: EvaluationRepository, clock=utc_now):
        self.repository = repository
        self.clock = clock

    def existing(self, user_id: str, session_id: UUID, submission: EvaluationInput) -> EvaluationResult | None:
        previous = self.repository.find(user_id, session_id)
        return require_same_submission(previous, submission).result() if previous else None

    def save(self, user_id: str, submission: EvaluationInput, **result_fields) -> EvaluationResult:
        session = EvaluationSession(user_id=user_id, submission=submission, created_at=self.clock(), **result_fields)
        return self.repository.save(session).result()

    def get(self, user_id: str, session_id: UUID) -> EvaluationSession:
        session = self.repository.find(user_id, session_id)
        if session is None:
            raise SessionNotFoundError("Sesión de evaluación no encontrada")
        return session

    def page(self, user_id: str, limit: int, cursor: UUID | None):
        return self.repository.page(user_id, limit, cursor)


@lru_cache
def get_evaluations_service() -> EvaluationsService:
    settings = get_settings()
    try:
        if settings.evaluations_backend == "firestore":
            return EvaluationsService(FirestoreEvaluationRepository(
                get_firestore_client(), settings.firestore_timeout_seconds,
            ))
        path = settings.evaluations_db_path
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        return EvaluationsService(SQLiteEvaluationRepository(path))
    except (OSError, sqlite3.DatabaseError, FirebaseConfigurationError, GoogleAuthError,
            GoogleAPICallError, EvaluationStorageError) as exc:
        logger.warning("Historial no disponible (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="El historial de evaluaciones no está disponible") from exc
