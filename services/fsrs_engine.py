"""Motor puro FSRS. Las fechas se normalizan a UTC antes de programar repasos."""

from datetime import datetime, timezone
from uuid import UUID

from fsrs import Card, Rating, ReviewLog, Scheduler


class ReviewTimeError(ValueError):
    """El reloj no permite avanzar la revisión de forma cronológica."""


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("La fecha debe incluir zona horaria")
    return value.astimezone(timezone.utc)


class FsrsEngine:
    def __init__(self, desired_retention: float = 0.9):
        # Intervalos reproducibles; se conservan los pesos y pasos de FSRS.
        self.scheduler = Scheduler(desired_retention=desired_retention, enable_fuzzing=False)

    def new_card(self, card_id: UUID, at: datetime) -> Card:
        return Card(card_id=card_id.int, due=utc_datetime(at))

    def review(
        self, card: Card, rating: int, at: datetime, duration_ms: int | None = None
    ) -> tuple[Card, ReviewLog]:
        if type(rating) is not int or not 1 <= rating <= 4:
            raise ValueError("La calificación FSRS debe ser un entero de 1 a 4")
        at = utc_datetime(at)
        if card.last_review is not None and at <= card.last_review:
            raise ReviewTimeError("La revisión debe ser posterior a la última registrada")
        return self.scheduler.review_card(
            card, Rating(rating), review_datetime=at, review_duration=duration_ms
        )
