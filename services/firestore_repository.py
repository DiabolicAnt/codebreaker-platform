"""Repositorio Firestore: unicidad por concepto y repasos atómicos e idempotentes."""

from datetime import datetime
from functools import wraps
from hashlib import sha256
from uuid import UUID, uuid4

from fsrs import Card
from google.api_core.exceptions import Aborted, GoogleAPICallError, RetryError
from google.auth.exceptions import GoogleAuthError
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from models.flashcards import DraftCard, Flashcard, FlashcardsBatch, ReviewFlashcardRequest, ReviewResult, concept_key
from services.flashcard_repository import CardNotFoundError, ReviewConflictError, StorageUnavailableError, public_card
from services.fsrs_engine import FsrsEngine, ReviewTimeError, utc_datetime


def document_key(value: str) -> str:
    # Un UID o concepto puede contener '/' u otros caracteres no aptos para una ruta.
    return sha256(value.encode("utf-8")).hexdigest()


def storage_errors(operation):
    @wraps(operation)
    def wrapped(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (GoogleAPICallError, RetryError, GoogleAuthError) as exc:
            raise StorageUnavailableError("Firestore no está disponible") from exc
        except ValueError as exc:
            # El SDK envuelve el agotamiento de reintentos transaccionales en ValueError.
            if isinstance(exc.__cause__, Aborted):
                raise StorageUnavailableError("Firestore no está disponible") from exc
            raise
    return wrapped


class FirestoreFlashcardRepository:
    def __init__(self, client, timeout: float = 10, *, root_collection: str = "usuarios"):
        self.client = client
        self.timeout = timeout
        self.root_collection = root_collection

    def owner(self, user_id: str):
        return self.client.collection(self.root_collection).document(document_key(user_id))

    def read(self, reference, transaction=None):
        return reference.get(transaction=transaction, retry=None, timeout=self.timeout)

    @storage_errors
    def create(self, user_id: str, drafts: list[DraftCard], at: datetime, engine: FsrsEngine) -> FlashcardsBatch:
        if not drafts:
            return FlashcardsBatch(cards=[], created_count=0, reused_count=0)
        owner = self.owner(user_id)
        # UUIDs estables entre reintentos; no efectos externos dentro de la transacción.
        entries = {concept_key(d.concept): (d, uuid4()) for d in drafts}

        @firestore.transactional
        def operation(transaction):
            mappings = {
                key: self.read(owner.collection("conceptos").document(document_key(key)), transaction)
                for key in entries
            }
            rows = {}
            for key, mapping in mappings.items():
                if mapping.exists:
                    reference = owner.collection("flashcards").document(mapping.to_dict()["card_id"])
                    snapshot = self.read(reference, transaction)
                    if not snapshot.exists:
                        raise StorageUnavailableError("Índice de conceptos inconsistente")
                    rows[key] = snapshot.to_dict()
            # Firestore exige completar TODAS las lecturas antes de la primera escritura.
            created = 0
            for key, (draft, card_id) in entries.items():
                if key in rows:
                    continue
                schedule = engine.new_card(card_id, at)
                row = {
                    "card_id": str(card_id), "user_id": user_id, "concept_key": key,
                    "concept": draft.concept, "question": draft.question, "answer": draft.answer,
                    "schedule_json": schedule.to_json(), "due_at": utc_datetime(schedule.due),
                    "created_at": utc_datetime(at), "updated_at": utc_datetime(at), "version": 0,
                }
                transaction.create(owner.collection("flashcards").document(str(card_id)), row)
                transaction.create(owner.collection("conceptos").document(document_key(key)),
                                   {"card_id": str(card_id)})
                rows[key] = row
                created += 1
            cards = [public_card(rows[concept_key(draft.concept)]) for draft in drafts]
            return FlashcardsBatch(cards=cards, created_count=created, reused_count=len(cards) - created)

        return operation(self.client.transaction(max_attempts=5))

    @storage_errors
    def get(self, user_id: str, card_id: UUID) -> Flashcard:
        snapshot = self.read(self.owner(user_id).collection("flashcards").document(str(card_id)))
        if not snapshot.exists:
            raise CardNotFoundError("Tarjeta no encontrada")
        return public_card(snapshot.to_dict())

    @storage_errors
    def due(self, user_id: str, at: datetime, limit: int) -> list[Flashcard]:
        query = (self.owner(user_id).collection("flashcards")
                 .where(filter=FieldFilter("due_at", "<=", utc_datetime(at)))
                 .order_by("due_at").order_by("__name__").limit(limit))
        return [public_card(doc.to_dict()) for doc in query.stream(retry=None, timeout=self.timeout)]

    @storage_errors
    def review(self, request: ReviewFlashcardRequest, at: datetime, engine: FsrsEngine) -> ReviewResult:
        owner = self.owner(request.user_id)
        review_ref = owner.collection("repasos").document(str(request.review_id))
        card_ref = owner.collection("flashcards").document(str(request.card_id))
        request_json = request.model_dump_json()

        @firestore.transactional
        def operation(transaction):
            previous = self.read(review_ref, transaction)
            if previous.exists:
                saved = previous.to_dict()
                if saved["request_json"] != request_json:
                    raise ReviewConflictError("El review_id ya fue usado con otros datos")
                return ReviewResult.model_validate_json(saved["result_json"])
            snapshot = self.read(card_ref, transaction)
            if not snapshot.exists:
                raise CardNotFoundError("Tarjeta no encontrada")
            row = snapshot.to_dict()
            if row["version"] != request.expected_version:
                raise ReviewConflictError("La versión de la tarjeta cambió. Consulta su estado actual")
            reviewed_at = utc_datetime(at)
            if reviewed_at < row["created_at"]:
                raise ReviewTimeError("La revisión no puede ser anterior a la creación")
            schedule, log = engine.review(Card.from_json(row["schedule_json"]), request.rating,
                                          reviewed_at, request.review_duration_ms)
            changes = {"schedule_json": schedule.to_json(), "due_at": utc_datetime(schedule.due),
                       "updated_at": reviewed_at, "version": row["version"] + 1}
            result = ReviewResult(review_id=request.review_id, rating=request.rating,
                                  reviewed_at=reviewed_at, card=public_card(dict(row, **changes)))
            transaction.update(card_ref, changes)
            transaction.create(review_ref, {
                "card_id": str(request.card_id), "request_json": request_json,
                "result_json": result.model_dump_json(), "reviewed_at": reviewed_at,
                "fsrs_log_json": log.to_json(),
            })
            return result

        return operation(self.client.transaction(max_attempts=5))
