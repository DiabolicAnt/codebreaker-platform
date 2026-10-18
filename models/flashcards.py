"""Contratos públicos; el estado interno de FSRS no se acepta desde el cliente."""

import unicodedata
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator


UserId = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Concept = Annotated[str, StringConstraints(min_length=1, max_length=200)]
RatingValue = Annotated[int, Field(ge=1, le=4, strict=True)]


def concept_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


class DraftCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept: Concept
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(min_length=1, max_length=4000)

    @field_validator("concept", "question", "answer")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El texto no puede estar vacío")
        return value.strip()


class CreateFlashcardsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UserId | None = None
    cards: list[DraftCard] = Field(min_length=1, max_length=20)

    @field_validator("cards")
    @classmethod
    def unique_concepts(cls, cards: list[DraftCard]) -> list[DraftCard]:
        keys = [concept_key(card.concept) for card in cards]
        if len(set(keys)) != len(keys):
            raise ValueError("Envía una sola tarjeta por concepto")
        return cards


class GenerateFlashcardsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UserId | None = None
    knowledge_gaps: list[Concept] = Field(max_length=5)
    context: str = Field(default="", max_length=8000)

    @field_validator("knowledge_gaps")
    @classmethod
    def clean_concepts(cls, concepts: list[str]) -> list[str]:
        cleaned = []
        seen = set()
        for concept in concepts:
            if not concept.strip():
                raise ValueError("El concepto no puede estar vacío")
            key = concept_key(concept)
            if key not in seen:
                cleaned.append(concept.strip())
                seen.add(key)
        return cleaned


class Flashcard(BaseModel):
    card_id: UUID
    user_id: UserId
    concept: str
    question: str
    answer: str
    state: Literal["learning", "review", "relearning"]
    next_review: AwareDatetime
    last_review: AwareDatetime | None
    stability: float | None
    difficulty: float | None
    version: int = Field(ge=0)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class FlashcardsBatch(BaseModel):
    cards: list[Flashcard]
    created_count: int
    reused_count: int


class DueFlashcards(BaseModel):
    user_id: UserId
    as_of: AwareDatetime
    cards: list[Flashcard]


class ReviewFlashcardBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UserId | None = None
    card_id: UUID
    review_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    rating: RatingValue
    review_duration_ms: int | None = Field(default=None, ge=0, le=3_600_000, strict=True)


class ReviewFlashcardRequest(ReviewFlashcardBody):
    """Comando interno: el router siempre inyecta el UID verificado."""

    user_id: UserId


class ReviewResult(BaseModel):
    review_id: UUID
    rating: RatingValue
    reviewed_at: AwareDatetime
    card: Flashcard
