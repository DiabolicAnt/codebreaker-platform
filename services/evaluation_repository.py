"""Persistencia de sesiones inmutables, con idempotencia por usuario y UUID."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol
from uuid import UUID

from models.evaluations import EvaluationInput, EvaluationPage, EvaluationSession, EvaluationSummary
from services.flashcard_repository import timestamp


class SessionNotFoundError(LookupError):
    pass


class SessionConflictError(ValueError):
    pass


class EvaluationStorageError(RuntimeError):
    pass


class EvaluationRepository(Protocol):
    def find(self, user_id: str, session_id: UUID) -> EvaluationSession | None: ...
    def save(self, session: EvaluationSession) -> EvaluationSession: ...
    def page(self, user_id: str, limit: int, cursor: UUID | None) -> EvaluationPage: ...


def require_same_submission(session: EvaluationSession, submission: EvaluationInput) -> EvaluationSession:
    if session.submission != submission:
        raise SessionConflictError("El session_id ya fue usado con otros datos")
    return session


def build_page(rows: list[EvaluationSummary], limit: int) -> EvaluationPage:
    has_more = len(rows) > limit
    selected = rows[:limit]
    return EvaluationPage(sessions=selected, next_cursor=selected[-1].session_id if has_more else None)


class SQLiteEvaluationRepository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS evaluation_sessions (
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    session_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    PRIMARY KEY (user_id, session_id)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS evaluation_history ON evaluation_sessions(user_id, created_at DESC, session_id DESC)")

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            yield connection
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def find(self, user_id: str, session_id: UUID) -> EvaluationSession | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT session_json FROM evaluation_sessions WHERE user_id=? AND session_id=?",
                (user_id, str(session_id)),
            ).fetchone()
        return EvaluationSession.model_validate_json(row["session_json"]) if row else None

    def save(self, session: EvaluationSession) -> EvaluationSession:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT session_json FROM evaluation_sessions WHERE user_id=? AND session_id=?",
                (session.user_id, str(session.session_id)),
            ).fetchone()
            if previous:
                return require_same_submission(EvaluationSession.model_validate_json(previous["session_json"]), session.submission)
            connection.execute(
                "INSERT INTO evaluation_sessions VALUES (?, ?, ?, ?, ?)",
                (session.user_id, str(session.session_id), timestamp(session.created_at),
                 session.model_dump_json(), EvaluationSummary.from_session(session).model_dump_json()),
            )
            connection.commit()
        return session

    def page(self, user_id: str, limit: int, cursor: UUID | None) -> EvaluationPage:
        with self.connection() as connection:
            values = [user_id]
            condition = ""
            if cursor is not None:
                anchor = connection.execute(
                    "SELECT created_at FROM evaluation_sessions WHERE user_id=? AND session_id=?",
                    (user_id, str(cursor)),
                ).fetchone()
                if anchor is None:
                    raise SessionNotFoundError("Sesión de evaluación no encontrada")
                condition = " AND (created_at < ? OR (created_at = ? AND session_id < ?))"
                values.extend([anchor["created_at"], anchor["created_at"], str(cursor)])
            values.append(limit + 1)
            rows = connection.execute(
                "SELECT summary_json FROM evaluation_sessions WHERE user_id=?" + condition +
                " ORDER BY created_at DESC, session_id DESC LIMIT ?", values,
            ).fetchall()
        return build_page([EvaluationSummary.model_validate_json(row["summary_json"]) for row in rows], limit)
