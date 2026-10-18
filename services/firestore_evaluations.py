"""Historial en Firestore bajo el mismo espacio de usuario que las flashcards."""

from functools import wraps
from uuid import UUID

from google.api_core.exceptions import Aborted, GoogleAPICallError, RetryError
from google.auth.exceptions import GoogleAuthError
from google.cloud import firestore

from models.evaluations import EvaluationPage, EvaluationSession, EvaluationSummary
from services.evaluation_repository import EvaluationStorageError, SessionNotFoundError, build_page, require_same_submission
from services.firestore_repository import document_key


def storage_errors(operation):
    @wraps(operation)
    def wrapped(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (GoogleAPICallError, RetryError, GoogleAuthError) as exc:
            raise EvaluationStorageError("El historial no está disponible") from exc
        except ValueError as exc:
            if isinstance(exc.__cause__, Aborted):
                raise EvaluationStorageError("El historial no está disponible") from exc
            raise
    return wrapped


class FirestoreEvaluationRepository:
    def __init__(self, client, timeout: float = 10, *, root_collection: str = "usuarios"):
        self.client = client
        self.timeout = timeout
        self.root_collection = root_collection

    def collection(self, user_id: str):
        return self.client.collection(self.root_collection).document(document_key(user_id)).collection("sesiones")

    def read(self, reference, transaction=None):
        return reference.get(transaction=transaction, retry=None, timeout=self.timeout)

    @storage_errors
    def find(self, user_id: str, session_id: UUID) -> EvaluationSession | None:
        snapshot = self.read(self.collection(user_id).document(str(session_id)))
        return EvaluationSession.model_validate_json(snapshot.to_dict()["session_json"]) if snapshot.exists else None

    @storage_errors
    def save(self, session: EvaluationSession) -> EvaluationSession:
        reference = self.collection(session.user_id).document(str(session.session_id))

        @firestore.transactional
        def operation(transaction):
            previous = self.read(reference, transaction)
            if previous.exists:
                saved = EvaluationSession.model_validate_json(previous.to_dict()["session_json"])
                return require_same_submission(saved, session.submission)
            transaction.create(reference, {
                "created_at": session.created_at,
                "session_json": session.model_dump_json(),
                "summary_json": EvaluationSummary.from_session(session).model_dump_json(),
            })
            return session

        return operation(self.client.transaction(max_attempts=5))

    @storage_errors
    def page(self, user_id: str, limit: int, cursor: UUID | None) -> EvaluationPage:
        collection = self.collection(user_id)
        query = (collection.order_by("created_at", direction=firestore.Query.DESCENDING)
                 .order_by("__name__", direction=firestore.Query.DESCENDING))
        if cursor is not None:
            anchor = self.read(collection.document(str(cursor)))
            if not anchor.exists:
                raise SessionNotFoundError("Sesión de evaluación no encontrada")
            query = query.start_after(anchor)
        # La lista no descarga código ni explicación. El detalle se consulta por ID.
        rows = query.select(["summary_json"]).limit(limit + 1).stream(retry=None, timeout=self.timeout)
        return build_page([EvaluationSummary.model_validate_json(row.to_dict()["summary_json"]) for row in rows], limit)
