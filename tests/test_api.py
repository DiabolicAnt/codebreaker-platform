import unittest
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from config import Settings
from main import app
from services.llm_judge import CoherenceJudge, CoherenceResult, get_judge


class ApiTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_judge] = lambda: CoherenceJudge(Settings())
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()

    def test_app_starts_and_openapi_is_available(self):
        self.assertEqual(self.client.get("/").json()["status"], "ok")
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/evaluator/analyze", response.json()["paths"])

    def test_no_explanation_means_unknown_not_good(self):
        response = self.client.post("/evaluator/analyze", json={"code": "def f(x):\n    return x + 1"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["coherence"]["status"], "insufficient_evidence")
        self.assertIsNone(data["bad_explanation_flag"])
        self.assertIsNone(data["coherence"]["bad_explanation"])

    def test_without_api_key_static_metrics_still_work(self):
        response = self.client.post("/evaluator/analyze", json={"code": "x=1+2", "explanation": "Sumo uno y dos"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["coherence"]["status"], "unavailable")
        self.assertGreater(response.json()["complexity"]["halstead_effort"], 0)

    def test_evaluated_result_contains_gaps_and_bad_explanation(self):
        judge = AsyncMock()
        judge.evaluate.return_value = CoherenceResult(
            status="evaluated", score=2, reasoning="Confunde suma y multiplicación.",
            bad_explanation=True, knowledge_gaps=["Operadores aritméticos"],
            source="anthropic", model="claude-sonnet-4-6",
        )
        app.dependency_overrides[get_judge] = lambda: judge
        response = self.client.post("/evaluator/analyze", json={"code": "x=1+2", "explanation": "Multiplico"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["bad_explanation_flag"])
        self.assertTrue(data["coherence"]["bad_explanation"])
        self.assertEqual(data["coherence"]["knowledge_gaps"], ["Operadores aritméticos"])
        self.assertIn("Confunde", data["feedback"])

    def test_invalid_submissions_do_not_reach_judge(self):
        judge = AsyncMock()
        app.dependency_overrides[get_judge] = lambda: judge
        for submission, status in [
            ({"code": " \n"}, 400), ({"code": "def f(:"}, 400),
            ({"code": "x=1", "language": "javascript"}, 422),
            ({"code": "x" * 40001}, 422),
            ({"code": "x=1", "explanation": "x" * 20001}, 422),
        ]:
            with self.subTest(status=status):
                response = self.client.post("/evaluator/analyze", json=submission)
                self.assertEqual(response.status_code, status)
        judge.evaluate.assert_not_awaited()

    def test_csharp_alias_via_http(self):
        response = self.client.post("/evaluator/analyze", json={
            "code": "class Demo { int F(int x) { return x + 1; } }", "language": "C#"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["complexity"]["functions"]), 1)
        self.assertIsNone(response.json()["complexity"]["halstead_effort"])

    def test_cors_only_allows_configured_frontend(self):
        for origin, allowed in [("http://localhost:5173", True), ("https://unknown.example", False)]:
            with self.subTest(origin=origin):
                response = self.client.options("/evaluator/analyze", headers={
                    "Origin": origin, "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                })
                self.assertEqual("access-control-allow-origin" in response.headers, allowed)
