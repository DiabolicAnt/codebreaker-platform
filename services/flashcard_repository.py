"""Persistencia local de desarrollo con transacciones y control de versión."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from fsrs import Card

from models.flashcards import (
    DraftCard, Flashcard, FlashcardsBatch, ReviewFlashcardRequest, ReviewResult, concept_key,
)
from services.fsrs_engine import FsrsEngine, ReviewTimeError, utc_datetime


class CardNotFoundError(LookupError):
    pass


class ReviewConflictError(ValueError):
    pass


class StorageUnavailableError(RuntimeError):
    pass


class FlashcardRepository(Protocol):
    def create(self, user_id: str, drafts: list[DraftCard], at: datetime, engine: FsrsEngine) -> FlashcardsBatch: ...
    def get(self, user_id: str, card_id: UUID) -> Flashcard: ...
    def due(self, user_id: str, at: datetime, limit: int) -> list[Flashcard]: ...
    def review(self, request: ReviewFlashcardRequest, at: datetime, engine: FsrsEngine) -> ReviewResult: ...


def timestamp(value: datetime) -> str:
    # Todas las fechas tienen igual zona y precisión para comparaciones en SQLite.
    return utc_datetime(value).isoformat(timespec="microseconds")


def public_card(row: sqlite3.Row) -> Flashcard:
    card = Card.from_json(row["schedule_json"])
    return Flashcard(
        card_id=row["card_id"], user_id=row["user_id"], concept=row["concept"],
        question=row["question"], answer=row["answer"], state=card.state.name.lower(),
        next_review=card.due, last_review=card.last_review, stability=card.stability,
        difficulty=card.difficulty, version=row["version"], created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SQLiteFlashcardRepository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise sqlite3.DatabaseError("Versión de almacenamiento no soportada")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS flashcards (
                    card_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    concept_key TEXT NOT NULL,
                    concept TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    schedule_json TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(user_id, concept_key)
                );
                CREATE INDEX IF NOT EXISTS flashcards_due ON flashcards(user_id, due_at, card_id);
                CREATE TABLE IF NOT EXISTS flashcard_reviews (
                    user_id TEXT NOT NULL,
                    review_id TEXT NOT NULL,
                    card_id TEXT NOT NULL REFERENCES flashcards(card_id),
                    request_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    fsrs_log_json TEXT NOT NULL,
                    PRIMARY KEY(user_id, review_id)
                );
                PRAGMA user_version=1;
                COMMIT;
            """)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def create(self, user_id: str, drafts: list[DraftCard], at: datetime, engine: FsrsEngine) -> FlashcardsBatch:
        created = 0
        cards = []
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for draft in drafts:
                key = concept_key(draft.concept)
                row = connection.execute(
                    "SELECT * FROM flashcards WHERE user_id=? AND concept_key=?", (user_id, key)
                ).fetchone()
                if row is None:
                    card_id = uuid4()
                    card = engine.new_card(card_id, at)
                    connection.execute(
                        """INSERT INTO flashcards
                           (card_id, user_id, concept_key, concept, question, answer, schedule_json,
                            due_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (str(card_id), user_id, key, draft.concept, draft.question, draft.answer,
                         card.to_json(), timestamp(card.due), timestamp(at), timestamp(at)),
                    )
                    row = connection.execute("SELECT * FROM flashcards WHERE card_id=?", (str(card_id),)).fetchone()
                    created += 1
                # Un concepto repetido conserva su contenido y todo su progreso.
                cards.append(public_card(row))
            connection.commit()
        return FlashcardsBatch(cards=cards, created_count=created, reused_count=len(cards) - created)

    def get(self, user_id: str, card_id: UUID) -> Flashcard:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM flashcards WHERE user_id=? AND card_id=?", (user_id, str(card_id))
            ).fetchone()
        if row is None:
            raise CardNotFoundError("Tarjeta no encontrada")
        return public_card(row)

    def due(self, user_id: str, at: datetime, limit: int) -> list[Flashcard]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM flashcards WHERE user_id=? AND due_at<=? ORDER BY due_at, card_id LIMIT ?",
                (user_id, timestamp(at), limit),
            ).fetchall()
        return [public_card(row) for row in rows]

    def review(self, request: ReviewFlashcardRequest, at: datetime, engine: FsrsEngine) -> ReviewResult:
        request_json = request.model_dump_json()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT * FROM flashcard_reviews WHERE user_id=? AND review_id=?",
                (request.user_id, str(request.review_id)),
            ).fetchone()
            if previous is not None:
                if previous["request_json"] != request_json:
                    raise ReviewConflictError("El review_id ya fue usado con otros datos")
                return ReviewResult.model_validate_json(previous["result_json"])

            row = connection.execute(
                "SELECT * FROM flashcards WHERE user_id=? AND card_id=?",
                (request.user_id, str(request.card_id)),
            ).fetchone()
            if row is None:
                raise CardNotFoundError("Tarjeta no encontrada")
            if row["version"] != request.expected_version:
                raise ReviewConflictError("La versión de la tarjeta cambió. Consulta su estado actual")
            at = utc_datetime(at)
            if at < datetime.fromisoformat(row["created_at"]):
                raise ReviewTimeError("La revisión no puede ser anterior a la creación")
            card, log = engine.review(
                Card.from_json(row["schedule_json"]), request.rating, at, request.review_duration_ms
            )
            connection.execute(
                """UPDATE flashcards SET schedule_json=?, due_at=?, updated_at=?, version=version+1
                   WHERE user_id=? AND card_id=?""",
                (card.to_json(), timestamp(card.due), timestamp(at), request.user_id, str(request.card_id)),
            )
            updated = connection.execute("SELECT * FROM flashcards WHERE card_id=?", (str(request.card_id),)).fetchone()
            result = ReviewResult(
                review_id=request.review_id, rating=request.rating, reviewed_at=at, card=public_card(updated)
            )
            connection.execute(
                """INSERT INTO flashcard_reviews
                   (user_id, review_id, card_id, request_json, result_json, reviewed_at, fsrs_log_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (request.user_id, str(request.review_id), str(request.card_id), request_json,
                 result.model_dump_json(), timestamp(at), log.to_json()),
            )
            connection.commit()
        return result
