import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from config import Settings
from main import app
from models.flashcards import DraftCard
from services.flashcard_generator import FlashcardGenerator, get_flashcard_generator
from services.flashcard_repository import SQLiteFlashcardRepository
from services.flashcards import FlashcardsService, get_flashcards_service
from services.fsrs_engine import FsrsEngine
from tests.helpers import TemporaryTestDirectory
from services.auth import AuthenticatedUser, get_current_user


class FlashcardApiTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(uid="caleb")
        self.temp = TemporaryTestDirectory()
        self.at = datetime(2026, 10, 4, 18, tzinfo=timezone.utc)
        self.repository = SQLiteFlashcardRepository(Path(self.temp.name) / "cards.sqlite3")
        self.service = FlashcardsService(self.repository, FsrsEngine(), lambda: self.at)
        app.dependency_overrides[get_flashcards_service] = lambda: self.service
        app.dependency_overrides[get_flashcard_generator] = lambda: FlashcardGenerator(Settings())
        self.client = TestClient(app)
        self.draft = {"concept": "Bucles", "question": "¿Qué hacen?", "answer": "Repiten una operación."}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.temp.cleanup()

    def create(self, user="caleb"):
        response = self.client.post("/flashcards/", json={"user_id": user, "cards": [self.draft]})
        self.assertEqual(response.status_code, 200)
        return response.json()["cards"][0]

    def request(self, card, **overrides):
        values = {"user_id": card["user_id"], "card_id": card["card_id"], "review_id": str(uuid4()), "rating": 3, "expected_version": card["version"]}
        values.update(overrides)
        return values

    def test_full_create_due_review_and_retry_flow(self):
        card = self.create()
        due = self.client.get("/flashcards/due/caleb").json()
        self.assertEqual(due["cards"][0]["card_id"], card["card_id"])
        self.at += timedelta(seconds=1)
        request = self.request(card, review_duration_ms=1400)
        response = self.client.post("/flashcards/review", json=request)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["card"]["version"], 1)
        self.assertEqual(self.client.get("/flashcards/due/caleb").json()["cards"], [])
        self.assertEqual(self.client.post("/flashcards/review", json=request).json(), result)
        current = self.client.get(f"/flashcards/caleb/{card['card_id']}")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json(), result["card"])

    def test_duplicate_creation_does_not_reset_state(self):
        card = self.create()
        self.at += timedelta(seconds=1)
        updated = self.client.post("/flashcards/review", json=self.request(card)).json()["card"]
        response = self.client.post("/flashcards/", json={"user_id": "caleb", "cards": [dict(self.draft, concept=" BUCLES ")]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reused_count"], 1)
        self.assertEqual(response.json()["cards"][0], updated)

    def test_user_filter_and_missing_card_return_404(self):
        card = self.create()
        self.assertEqual(self.client.get("/flashcards/due/lizeth").status_code, 403)
        self.assertEqual(self.client.get(f"/flashcards/lizeth/{card['card_id']}").status_code, 403)
        for values in [{"user_id": "lizeth"}, {"card_id": str(uuid4())}]:
            expected = 403 if "user_id" in values else 404
            self.assertEqual(self.client.post("/flashcards/review", json=self.request(card, **values)).status_code, expected)

    def test_conflict_on_stale_version_or_changed_retry(self):
        card = self.create()
        self.at += timedelta(seconds=1)
        request = self.request(card)
        self.assertEqual(self.client.post("/flashcards/review", json=request).status_code, 200)
        self.assertEqual(self.client.post("/flashcards/review", json=self.request(card)).status_code, 409)
        self.assertEqual(self.client.post("/flashcards/review", json=dict(request, rating=4)).status_code, 409)

    def test_invalid_review_ratings_types_versions_and_extra_state_are_rejected(self):
        card = self.create()
        invalid = [{"rating": 0}, {"rating": 5}, {"rating": True}, {"rating": "3"},
                   {"expected_version": -1}, {"expected_version": True},
                   {"review_duration_ms": -1}, {"review_duration_ms": "100"},
                   {"next_review": "2026-10-01T00:00:00Z"}, {"review_id": "not-uuid"}]
        for changes in invalid:
            with self.subTest(changes=changes):
                response = self.client.post("/flashcards/review", json=self.request(card, **changes))
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.service.get("caleb", UUID(card["card_id"])).version, 0)

    def test_invalid_card_content_duplicate_concepts_and_user_ids_are_rejected(self):
        invalid = [
            {"user_id": "caleb", "cards": []},
            {"user_id": "caleb", "cards": [dict(self.draft, question=" ")]},
            {"user_id": "caleb", "cards": [self.draft, dict(self.draft, concept="BUCLES")]},
            {"user_id": "caleb", "cards": [dict(self.draft, answer="x" * 4001)]},
        ]
        for body in invalid:
            with self.subTest(body=str(body)[:80]):
                self.assertEqual(self.client.post("/flashcards/", json=body).status_code, 422)
        self.assertEqual(self.service.due("caleb", 10).cards, [])

    def test_canonical_routes_and_uid_come_from_token(self):
        response = self.client.post("/flashcards/", json={"cards": [self.draft]})
        self.assertEqual(response.status_code, 200)
        card = response.json()["cards"][0]
        self.assertEqual(card["user_id"], "caleb")
        self.assertEqual(self.client.get("/flashcards/due").json()["cards"], [card])
        self.assertEqual(self.client.get(f"/flashcards/{card['card_id']}").json(), card)
        command = self.request(card)
        del command["user_id"]
        self.at += timedelta(seconds=1)
        self.assertEqual(self.client.post("/flashcards/review", json=command).status_code, 200)

    def test_other_authenticated_user_cannot_access_card(self):
        card = self.create()
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(uid="lizeth")
        self.assertEqual(self.client.get("/flashcards/due").json()["cards"], [])
        self.assertEqual(self.client.get(f"/flashcards/{card['card_id']}").status_code, 404)
        command = self.request(card)
        del command["user_id"]
        self.assertEqual(self.client.post("/flashcards/review", json=command).status_code, 404)

    def test_claimed_owner_is_checked_before_calling_generator(self):
        generator = AsyncMock()
        app.dependency_overrides[get_flashcard_generator] = lambda: generator
        response = self.client.post("/flashcards/generate", json={"user_id": "lizeth", "knowledge_gaps": ["Bucles"]})
        self.assertEqual(response.status_code, 403)
        generator.generate.assert_not_awaited()

    def test_due_limit_is_validated(self):
        for limit in [0, 201]:
            self.assertEqual(self.client.get(f"/flashcards/due/caleb?limit={limit}").status_code, 422)

    def test_generation_uses_gaps_and_keeps_concepts_unique(self):
        generator = AsyncMock()
        generator.generate.return_value = [DraftCard(**self.draft)]
        app.dependency_overrides[get_flashcard_generator] = lambda: generator
        response = self.client.post("/flashcards/generate", json={
            "user_id": "caleb", "knowledge_gaps": ["Bucles", " BUCLES "], "context": "Código del reto"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created_count"], 1)
        generator.generate.assert_awaited_once_with(["Bucles"], "Código del reto")

    def test_empty_gaps_do_not_create_cards_and_missing_key_returns_503(self):
        empty = self.client.post("/flashcards/generate", json={"user_id": "caleb", "knowledge_gaps": []})
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["cards"], [])
        response = self.client.post("/flashcards/generate", json={"user_id": "caleb", "knowledge_gaps": ["Bucles"]})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.service.due("caleb", 10).cards, [])

    def test_invalid_generation_input_is_rejected_before_provider_call(self):
        generator = AsyncMock()
        app.dependency_overrides[get_flashcard_generator] = lambda: generator
        for gaps in [[" "], ["x"] * 6, ["x" * 201]]:
            self.assertEqual(self.client.post("/flashcards/generate", json={"user_id": "caleb", "knowledge_gaps": gaps}).status_code, 422)
        generator.generate.assert_not_awaited()

    def test_storage_failure_returns_503(self):
        with patch.object(self.repository, "due", side_effect=sqlite3.OperationalError("private-path")):
            response = self.client.get("/flashcards/due/caleb")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private-path", response.text)

    def test_openapi_exposes_all_flashcard_contracts(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        for path in ["/flashcards/", "/flashcards/generate", "/flashcards/review", "/flashcards/due/{user_id}", "/flashcards/{user_id}/{card_id}"]:
            self.assertIn(path, paths)
