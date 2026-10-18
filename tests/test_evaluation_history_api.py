import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from main import app
from services.auth import AuthenticatedUser, get_current_user
from services.evaluation_repository import EvaluationStorageError, SQLiteEvaluationRepository
from services.evaluations import EvaluationsService, get_evaluations_service
from services.llm_judge import CoherenceResult, get_judge
from tests.helpers import TemporaryTestDirectory


class EvaluationHistoryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryTestDirectory()
        self.repository = SQLiteEvaluationRepository(Path(self.temp.name) / "sessions.sqlite3")
        self.at = datetime(2026, 10, 18, 18, tzinfo=timezone.utc)
        self.service = EvaluationsService(self.repository, lambda: self.at)
        self.judge = AsyncMock()
        self.judge.evaluate.return_value = CoherenceResult(
            status="evaluated", score=4, reasoning="Coincide.", bad_explanation=False,
        )
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(uid="caleb")
        app.dependency_overrides[get_evaluations_service] = lambda: self.service
        app.dependency_overrides[get_judge] = lambda: self.judge
        self.client = TestClient(app)
        self.body = {"code": "x=1", "explanation": "Asigno uno.", "language": " PY "}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.temp.cleanup()

    def evaluate(self, **changes):
        response = self.client.post("/evaluator/analyze", json=dict(self.body, **changes))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_analyze_persists_detail_and_summary_without_submission_text(self):
        result = self.evaluate()
        self.assertIsNotNone(UUID(result["session_id"]))
        self.assertEqual(result["created_at"], "2026-10-18T18:00:00Z")
        detail = self.client.get(f"/evaluator/sessions/{result['session_id']}").json()
        self.assertEqual(detail["submission"], dict(self.body, language="python"))
        self.assertEqual(detail["user_id"], "caleb")
        for field, value in result.items():
            self.assertEqual(detail[field], value)
        page = self.client.get("/evaluator/sessions").json()
        self.assertEqual(page["sessions"][0]["session_id"], result["session_id"])
        self.assertNotIn("submission", page["sessions"][0])
        self.assertNotIn("code", page["sessions"][0])
        self.assertIsNone(page["next_cursor"])

    def test_same_request_recovers_saved_response_without_calling_judge_again(self):
        session_id = str(uuid4())
        first = self.evaluate(session_id=session_id)
        self.evaluate(code="x=2")
        retry = self.evaluate(session_id=session_id, language="python")
        self.assertEqual(retry, first)
        self.assertEqual(self.judge.evaluate.await_count, 2)
        self.assertEqual(len(self.client.get("/evaluator/sessions").json()["sessions"]), 2)

    def test_reused_id_with_changed_submission_is_409(self):
        session_id = str(uuid4())
        self.evaluate(session_id=session_id)
        for changed in [{"code": "x=2"}, {"explanation": "Otra"}, {"language": "java"}]:
            response = self.client.post("/evaluator/analyze", json=dict(self.body, session_id=session_id, **changed))
            self.assertEqual(response.status_code, 409)
        self.judge.evaluate.assert_awaited_once()

    def test_other_user_cannot_read_session_or_use_its_cursor(self):
        result = self.evaluate()
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(uid="lizeth")
        self.assertEqual(self.client.get(f"/evaluator/sessions/{result['session_id']}").status_code, 404)
        self.assertEqual(self.client.get("/evaluator/sessions").json()["sessions"], [])
        self.assertEqual(self.client.get(f"/evaluator/sessions?cursor={result['session_id']}").status_code, 404)

    def test_same_id_for_another_user_is_a_separate_session(self):
        session_id = str(uuid4())
        self.evaluate(session_id=session_id)
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(uid="lizeth")
        self.evaluate(session_id=session_id, code="x=2")
        detail = self.client.get(f"/evaluator/sessions/{session_id}").json()
        self.assertEqual(detail["user_id"], "lizeth")
        self.assertEqual(detail["submission"]["code"], "x=2")

    def test_invalid_input_or_client_identity_and_time_are_not_saved(self):
        cases = [({"code": "def f(:"}, 400), ({"code": " "}, 400),
                 ({"user_id": "lizeth"}, 422), ({"created_at": "2020-01-01"}, 422),
                 ({"session_id": "bad-uuid"}, 422)]
        for changes, expected in cases:
            response = self.client.post("/evaluator/analyze", json=dict(self.body, **changes))
            self.assertEqual(response.status_code, expected)
        self.assertEqual(self.client.get("/evaluator/sessions").json()["sessions"], [])
        self.judge.evaluate.assert_not_awaited()

    def test_unknown_session_and_invalid_pagination(self):
        self.assertEqual(self.client.get(f"/evaluator/sessions/{uuid4()}").status_code, 404)
        for query in ["limit=0", "limit=101", "cursor=invalid"]:
            self.assertEqual(self.client.get("/evaluator/sessions?" + query).status_code, 422)

    def test_pagination_uses_time_and_uuid_without_duplicates(self):
        for value in range(1, 6):
            self.evaluate(session_id=str(UUID(int=value)))
        first = self.client.get("/evaluator/sessions?limit=2").json()
        second = self.client.get(f"/evaluator/sessions?limit=2&cursor={first['next_cursor']}").json()
        third = self.client.get(f"/evaluator/sessions?limit=2&cursor={second['next_cursor']}").json()
        ids = [row["session_id"] for page in (first, second, third) for row in page["sessions"]]
        self.assertEqual(ids, [str(UUID(int=value)) for value in range(5, 0, -1)])
        self.assertIsNone(third["next_cursor"])

    def test_no_assessment_remains_unknown_in_persisted_history(self):
        for status in ["unavailable", "insufficient_evidence"]:
            self.judge.evaluate.return_value = CoherenceResult(status=status, reasoning="Sin evaluación")
            result = self.evaluate()
            detail = self.client.get(f"/evaluator/sessions/{result['session_id']}").json()
            self.assertEqual(detail["coherence"]["status"], status)
            self.assertIsNone(detail["coherence"]["score"])
            self.assertIsNone(detail["bad_explanation_flag"])

    def test_read_failure_stops_before_judge_and_hides_private_details(self):
        with patch.object(self.repository, "find", side_effect=EvaluationStorageError("private-path")):
            response = self.client.post("/evaluator/analyze", json=self.body)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private-path", response.text)
        self.judge.evaluate.assert_not_awaited()

    def test_save_failure_is_not_reported_as_success(self):
        with patch.object(self.repository, "save", side_effect=sqlite3.OperationalError("private-path")):
            response = self.client.post("/evaluator/analyze", json=self.body)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private-path", response.text)
        self.assertEqual(self.client.get("/evaluator/sessions").json()["sessions"], [])

    def test_retry_recovers_a_committed_session_after_lost_acknowledgement(self):
        original_save = self.repository.save
        session_id = str(uuid4())

        def commit_then_disconnect(session):
            original_save(session)
            raise EvaluationStorageError("lost acknowledgement")

        with patch.object(self.repository, "save", side_effect=commit_then_disconnect):
            response = self.client.post("/evaluator/analyze", json=dict(self.body, session_id=session_id))
        self.assertEqual(response.status_code, 503)
        recovered = self.evaluate(session_id=session_id)
        self.assertEqual(recovered["session_id"], session_id)
        self.judge.evaluate.assert_awaited_once()
        self.assertEqual(len(self.client.get("/evaluator/sessions").json()["sessions"]), 1)
