import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from models.flashcards import DraftCard, ReviewFlashcardRequest
from services.flashcard_repository import CardNotFoundError, ReviewConflictError, SQLiteFlashcardRepository
from services.fsrs_engine import FsrsEngine, ReviewTimeError
from tests.helpers import TemporaryTestDirectory


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryTestDirectory()
        self.path = Path(self.temp.name) / "cards.sqlite3"
        self.repository = SQLiteFlashcardRepository(self.path)
        self.engine = FsrsEngine()
        self.at = datetime(2026, 10, 4, 18, tzinfo=timezone.utc)
        self.draft = DraftCard(concept="Bucles", question="¿Qué hace un bucle?", answer="Repite una operación.")

    def tearDown(self):
        self.temp.cleanup()

    def create(self, user="caleb"):
        return self.repository.create(user, [self.draft], self.at, self.engine).cards[0]

    def request(self, card, **overrides):
        values = dict(user_id=card.user_id, card_id=card.card_id, review_id=uuid4(), expected_version=card.version, rating=3)
        values.update(overrides)
        return ReviewFlashcardRequest(**values)

    def review_count(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute("SELECT COUNT(*) FROM flashcard_reviews").fetchone()[0]

    def test_cards_and_schedule_survive_repository_restart(self):
        card = self.create()
        result = self.repository.review(self.request(card), self.at + timedelta(seconds=1), self.engine)
        reopened = SQLiteFlashcardRepository(self.path)
        self.assertEqual(reopened.get("caleb", card.card_id), result.card)
        self.assertEqual(self.review_count(), 1)

    def test_due_boundary_and_user_filter(self):
        card = self.create()
        other = self.create("lizeth")
        self.assertEqual(self.repository.due("caleb", self.at - timedelta(microseconds=1), 10), [])
        self.assertEqual(self.repository.due("caleb", self.at, 10), [card])
        self.assertEqual(self.repository.due("lizeth", self.at, 10), [other])
        result = self.repository.review(self.request(card), self.at + timedelta(seconds=1), self.engine)
        self.assertEqual(self.repository.due("caleb", result.card.next_review - timedelta(microseconds=1), 10), [])
        self.assertEqual(self.repository.due("caleb", result.card.next_review, 10), [result.card])

    def test_duplicate_concept_reuses_card_without_resetting_progress(self):
        card = self.create()
        reviewed = self.repository.review(self.request(card), self.at + timedelta(seconds=1), self.engine).card
        changed_draft = DraftCard(concept=" BUCLES ", question="Otra pregunta", answer="Otra respuesta")
        batch = self.repository.create("caleb", [changed_draft], self.at + timedelta(days=1), self.engine)
        self.assertEqual(batch.created_count, 0)
        self.assertEqual(batch.reused_count, 1)
        self.assertEqual(batch.cards[0], reviewed)

    def test_exact_retry_does_not_review_twice_even_after_later_reviews(self):
        card = self.create()
        request = self.request(card)
        first = self.repository.review(request, self.at + timedelta(seconds=1), self.engine)
        second = self.repository.review(self.request(first.card), first.card.next_review, self.engine)
        retry = self.repository.review(request, self.at + timedelta(days=2), self.engine)
        self.assertEqual(retry, first)
        self.assertEqual(self.repository.get("caleb", card.card_id), second.card)
        self.assertEqual(self.review_count(), 2)

    def test_reusing_review_id_with_changed_data_is_conflict(self):
        card = self.create()
        request = self.request(card)
        first = self.repository.review(request, self.at + timedelta(seconds=1), self.engine)
        changed = request.model_copy(update={"rating": 4})
        with self.assertRaises(ReviewConflictError):
            self.repository.review(changed, self.at + timedelta(seconds=2), self.engine)
        self.assertEqual(self.repository.get("caleb", card.card_id), first.card)
        self.assertEqual(self.review_count(), 1)

    def test_other_user_and_unknown_cards_cannot_be_read_or_reviewed(self):
        card = self.create()
        for user, card_id in [("lizeth", card.card_id), ("caleb", uuid4())]:
            with self.subTest(user=user):
                with self.assertRaises(CardNotFoundError):
                    self.repository.get(user, card_id)
                with self.assertRaises(CardNotFoundError):
                    self.repository.review(self.request(card, user_id=user, card_id=card_id), self.at, self.engine)
        self.assertEqual(self.review_count(), 0)

    def test_stale_version_is_rejected_without_changing_state(self):
        card = self.create()
        first = self.repository.review(self.request(card), self.at + timedelta(seconds=1), self.engine)
        with self.assertRaises(ReviewConflictError):
            self.repository.review(self.request(card), self.at + timedelta(seconds=2), self.engine)
        self.assertEqual(self.repository.get("caleb", card.card_id), first.card)
        self.assertEqual(self.review_count(), 1)

    def test_creation_batch_rolls_back_if_any_card_fails(self):
        class BrokenEngine(FsrsEngine):
            calls = 0

            def new_card(self, card_id, at):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("Fallo simulado")
                return super().new_card(card_id, at)

        other = DraftCard(concept="Recursión", question="¿Qué es?", answer="Una función se llama a sí misma.")
        with self.assertRaises(RuntimeError):
            self.repository.create("caleb", [self.draft, other], self.at, BrokenEngine())
        self.assertEqual(self.repository.due("caleb", self.at, 10), [])

    def test_backward_clock_does_not_change_card(self):
        card = self.create()
        with self.assertRaises(ReviewTimeError):
            self.repository.review(self.request(card), self.at - timedelta(seconds=1), self.engine)
        self.assertEqual(self.repository.get("caleb", card.card_id), card)
        self.assertEqual(self.review_count(), 0)

    def test_failed_history_write_rolls_back_schedule_update(self):
        card = self.create()
        with self.repository.connection() as connection:
            connection.execute("""CREATE TRIGGER reject_review BEFORE INSERT ON flashcard_reviews
                                  BEGIN SELECT RAISE(ABORT, 'Fallo simulado'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.review(self.request(card), self.at + timedelta(seconds=1), self.engine)
        self.assertEqual(self.repository.get("caleb", card.card_id), card)
        self.assertEqual(self.review_count(), 0)

    def test_concurrent_reviews_of_same_version_allow_only_one_update(self):
        card = self.create()

        def attempt(_):
            try:
                return self.repository.review(self.request(card), self.at + timedelta(seconds=1), self.engine)
            except ReviewConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.repository.get("caleb", card.card_id).version, 1)
        self.assertEqual(self.review_count(), 1)

    def test_concurrent_creation_preserves_one_card_per_concept(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            batches = list(pool.map(lambda _: self.repository.create("caleb", [self.draft], self.at, self.engine), range(4)))
        self.assertEqual(sum(batch.created_count for batch in batches), 1)
        self.assertEqual(len({batch.cards[0].card_id for batch in batches}), 1)

    def test_concurrent_retry_of_same_request_creates_one_review(self):
        card = self.create()
        request = self.request(card)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.repository.review(request, self.at + timedelta(seconds=1), self.engine), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.review_count(), 1)
