"""Pruebas optativas contra Firestore real/emulado; sólo usan un espacio temporal propio.

FIRESTORE_INTEGRATION_TESTS=1 habilita escrituras y limpieza limitada a sus UID aleatorios.
La suite normal no usa credenciales ni hace llamadas a Google.
"""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from google.cloud.firestore_v1.transaction import Transaction

from models.flashcards import DraftCard, ReviewFlashcardRequest
from services.firebase import get_firestore_client
from services.firestore_repository import FirestoreFlashcardRepository
from services.flashcard_repository import CardNotFoundError, ReviewConflictError
from services.fsrs_engine import FsrsEngine, ReviewTimeError


@unittest.skipUnless(os.getenv("FIRESTORE_INTEGRATION_TESTS") == "1", "Firestore externo: habilitación explícita requerida")
class FirestoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.repository = FirestoreFlashcardRepository(
            get_firestore_client(), root_collection="codebreaker_integration_tests",
        )
        self.uid = "integration-" + str(uuid4())
        self.other_uid = "integration-" + str(uuid4())
        self.at = datetime.now(timezone.utc)
        self.engine = FsrsEngine()
        self.draft = DraftCard(concept="Bucles", question="¿Qué hace?", answer="Repite.")
        self.addCleanup(self.clean_up)

    def clean_up(self):
        for uid in (self.uid, self.other_uid):
            owner = self.repository.owner(uid)
            expected = "codebreaker_integration_tests/"
            if not owner.path.startswith(expected):
                raise RuntimeError("Limpieza fuera del espacio de pruebas")
            for collection in owner.collections():
                for document in collection.stream():
                    document.reference.delete()
            owner.delete()

    def card(self):
        return self.repository.create(self.uid, [self.draft], self.at, self.engine).cards[0]

    def request(self, card, **changes):
        values = dict(user_id=self.uid, card_id=card.card_id, review_id=uuid4(),
                      expected_version=card.version, rating=3)
        values.update(changes)
        return ReviewFlashcardRequest(**values)

    def history_count(self):
        return len(list(self.repository.owner(self.uid).collection("repasos").stream()))

    def test_create_due_deduplicate_and_reopen(self):
        card = self.card()
        self.assertEqual(self.repository.due(self.uid, self.at - timedelta(microseconds=1), 10), [])
        self.assertEqual(self.repository.due(self.uid, self.at, 10), [card])
        result = self.repository.review(self.request(card), self.at + timedelta(seconds=1), self.engine)
        batch = self.repository.create(self.uid, [DraftCard(concept=" BUCLES ", question="Otra", answer="Otra")], self.at, self.engine)
        self.assertEqual(batch.created_count, 0)
        self.assertEqual(batch.cards, [result.card])
        reopened = FirestoreFlashcardRepository(get_firestore_client(), root_collection="codebreaker_integration_tests")
        self.assertEqual(reopened.get(self.uid, card.card_id), result.card)
        self.assertEqual(reopened.due(self.uid, result.card.next_review, 10), [result.card])

    def test_retry_remains_identical_after_subsequent_review_and_changed_retry_conflicts(self):
        card = self.card()
        command = self.request(card)
        result = self.repository.review(command, self.at + timedelta(seconds=1), self.engine)
        later = self.repository.review(self.request(result.card), result.card.next_review, self.engine)
        self.assertEqual(self.repository.review(command, self.at + timedelta(days=1), self.engine), result)
        self.assertEqual(self.repository.get(self.uid, card.card_id), later.card)
        with self.assertRaises(ReviewConflictError):
            self.repository.review(command.model_copy(update={"rating": 4}), self.at, self.engine)
        self.assertEqual(self.history_count(), 2)

    def test_cross_user_and_unknown_cards_cannot_be_accessed(self):
        card = self.card()
        self.assertEqual(self.repository.due(self.other_uid, self.at, 10), [])
        with self.assertRaises(CardNotFoundError):
            self.repository.get(self.other_uid, card.card_id)
        with self.assertRaises(CardNotFoundError):
            self.repository.review(self.request(card, user_id=self.other_uid), self.at, self.engine)

    def test_stale_version_and_backward_clock_do_not_mutate(self):
        card = self.card()
        with self.assertRaises(ReviewConflictError):
            self.repository.review(self.request(card, expected_version=1), self.at, self.engine)
        with self.assertRaises(ReviewTimeError):
            self.repository.review(self.request(card), self.at - timedelta(seconds=1), self.engine)
        self.assertEqual(self.repository.get(self.uid, card.card_id), card)
        self.assertEqual(self.history_count(), 0)

    def test_concurrent_creations_preserve_one_concept(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            batches = list(pool.map(lambda _: self.repository.create(self.uid, [self.draft], self.at, self.engine), range(3)))
        self.assertEqual(sum(b.created_count for b in batches), 1)
        self.assertEqual(len({b.cards[0].card_id for b in batches}), 1)

    def test_concurrent_reviews_allow_one_version_update(self):
        card = self.card()

        def attempt(_):
            try:
                return self.repository.review(self.request(card), self.at + timedelta(seconds=1), self.engine)
            except ReviewConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(self.repository.get(self.uid, card.card_id).version, 1)
        self.assertEqual(self.history_count(), 1)

    def test_concurrent_same_retry_is_idempotent(self):
        card = self.card()
        command = self.request(card)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.repository.review(command, self.at + timedelta(seconds=1), self.engine), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.history_count(), 1)

    def test_history_failure_rolls_back_card_update(self):
        card = self.card()
        original = Transaction.create

        def fail_history(transaction, reference, data):
            if "/repasos/" in reference.path:
                raise RuntimeError("simulated history failure")
            return original(transaction, reference, data)

        with patch.object(Transaction, "create", new=fail_history):
            with self.assertRaises(RuntimeError):
                self.repository.review(self.request(card), self.at + timedelta(seconds=1), self.engine)
        self.assertEqual(self.repository.get(self.uid, card.card_id), card)
        self.assertEqual(self.history_count(), 0)

    def test_creation_reads_all_existing_cards_before_writes(self):
        card = self.card()
        draft = DraftCard(concept="Funciones", question="¿Qué hacen?", answer="Agrupan instrucciones.")
        batch = self.repository.create(self.uid, [draft, self.draft], self.at, self.engine)
        self.assertEqual(batch.created_count, 1)
        self.assertEqual(batch.reused_count, 1)
        self.assertEqual(batch.cards[1], card)

    def test_arbitrary_firebase_uid_is_not_interpreted_as_document_path(self):
        # Los UID de Firebase no están limitados a letras ASCII.
        unusual_uid = self.uid + "/ñ"
        self.assertNotEqual(self.repository.owner(unusual_uid).path, self.repository.owner(self.uid).path)
        self.assertEqual(len(self.repository.owner(unusual_uid).path.split("/")), 2)
