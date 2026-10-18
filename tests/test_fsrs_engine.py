import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fsrs import Card, State

from services.fsrs_engine import FsrsEngine, ReviewTimeError


class FsrsEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = FsrsEngine()
        self.at = datetime(2026, 10, 4, 18, tzinfo=timezone.utc)
        self.card = self.engine.new_card(uuid4(), self.at)

    def test_new_card_is_due_immediately_with_unique_identity(self):
        other = self.engine.new_card(uuid4(), self.at)
        self.assertEqual(self.card.due, self.at)
        self.assertEqual(self.card.state, State.Learning)
        self.assertIsNone(self.card.last_review)
        self.assertNotEqual(self.card.card_id, other.card_id)

    def test_each_rating_uses_real_fsrs_schedule_and_does_not_mutate_original(self):
        expected = {1: timedelta(minutes=1), 2: timedelta(minutes=5, seconds=30), 3: timedelta(minutes=10)}
        for rating in range(1, 5):
            with self.subTest(rating=rating):
                reviewed, log = self.engine.review(self.card, rating, self.at, 1200)
                self.assertGreater(reviewed.due, self.at)
                self.assertEqual(reviewed.last_review, self.at)
                self.assertEqual(log.rating.value, rating)
                self.assertEqual(log.review_duration, 1200)
                self.assertIsNotNone(reviewed.stability)
                if rating in expected:
                    self.assertEqual(reviewed.due - self.at, expected[rating])
                else:
                    self.assertEqual(reviewed.state, State.Review)
        self.assertIsNone(self.card.last_review)
        self.assertEqual(self.card.due, self.at)

    def test_learning_review_and_relearning_cycle(self):
        card, _ = self.engine.review(self.card, 3, self.at)
        self.assertEqual(card.state, State.Learning)
        card, _ = self.engine.review(card, 3, card.due)
        self.assertEqual(card.state, State.Review)
        card, _ = self.engine.review(card, 1, card.due)
        self.assertEqual(card.state, State.Relearning)

    def test_serialization_preserves_complete_schedule(self):
        card, _ = self.engine.review(self.card, 4, self.at)
        restored = Card.from_json(card.to_json())
        self.assertEqual(restored.to_dict(), card.to_dict())
        next_card, _ = self.engine.review(restored, 3, restored.due)
        self.assertGreater(next_card.due, restored.due)

    def test_non_utc_offset_is_normalized(self):
        local = datetime(2026, 10, 4, 12, tzinfo=timezone(timedelta(hours=-6)))
        card = self.engine.new_card(uuid4(), local)
        reviewed, _ = self.engine.review(card, 3, local)
        self.assertEqual(reviewed.last_review, self.at)
        self.assertEqual(reviewed.due.tzinfo, timezone.utc)

    def test_naive_time_and_invalid_ratings_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.new_card(uuid4(), datetime(2026, 10, 4))
        for rating in [0, 5, True, "3"]:
            with self.subTest(rating=rating), self.assertRaises(ValueError):
                self.engine.review(self.card, rating, self.at)

    def test_review_times_cannot_move_backwards_or_repeat(self):
        reviewed, _ = self.engine.review(self.card, 3, self.at)
        for at in [self.at, self.at - timedelta(seconds=1)]:
            with self.subTest(at=at), self.assertRaises(ReviewTimeError):
                self.engine.review(reviewed, 3, at)
