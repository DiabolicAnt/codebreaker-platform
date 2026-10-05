import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2
from anthropic import APIConnectionError, AsyncAnthropic

from config import Settings
from services.llm_judge import CoherenceJudge, JudgeAssessment


def fake_client(message=None, error=None):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.messages.parse = AsyncMock(return_value=message, side_effect=error)
    return client


class JudgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_blank_explanation_never_calls_provider(self):
        with patch("services.llm_judge.AsyncAnthropic") as factory:
            result = await CoherenceJudge(Settings(anthropic_api_key="test")).evaluate("x=1", " \n", {})
        factory.assert_not_called()
        self.assertEqual(result.status, "insufficient_evidence")
        self.assertIsNone(result.score)
        self.assertIsNone(result.bad_explanation)

    async def test_missing_key_never_calls_provider(self):
        with patch("services.llm_judge.AsyncAnthropic") as factory:
            result = await CoherenceJudge(Settings()).evaluate("x=1", "Asigno uno a x", {})
        factory.assert_not_called()
        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.bad_explanation)

    async def test_scores_map_to_flag_and_prompt_carries_all_inputs(self):
        for score in range(1, 6):
            with self.subTest(score=score):
                message = SimpleNamespace(
                    stop_reason="end_turn", parsed_output=JudgeAssessment(
                        score=score, reasoning="La explicación identifica la asignación.", knowledge_gaps=[]
                    )
                )
                client = fake_client(message)
                with patch("services.llm_judge.AsyncAnthropic", return_value=client):
                    result = await CoherenceJudge(Settings(anthropic_api_key="test")).evaluate(
                        "x=1", "Asigno uno a x", {"max_cyclomatic": 1}, "python"
                    )
                self.assertEqual(result.status, "evaluated")
                self.assertEqual(result.bad_explanation, score <= 2)
                self.assertEqual(result.source, "anthropic")
                request = client.messages.parse.call_args.kwargs
                payload = json.loads(request["messages"][0]["content"])
                self.assertEqual(payload["code"], "x=1")
                self.assertEqual(payload["explanation"], "Asigno uno a x")
                self.assertEqual(payload["complexity"]["max_cyclomatic"], 1)
                self.assertEqual(payload["language"], "python")
                self.assertIn("DATOS NO CONFIABLES", request["system"])
                client.__aexit__.assert_awaited_once()

    async def test_refusal_truncation_and_missing_output_are_unavailable(self):
        for reason, output in [("refusal", None), ("max_tokens", None), ("end_turn", None)]:
            with self.subTest(reason=reason):
                client = fake_client(SimpleNamespace(stop_reason=reason, parsed_output=output))
                with patch("services.llm_judge.AsyncAnthropic", return_value=client):
                    result = await CoherenceJudge(Settings(anthropic_api_key="test")).evaluate("x=1", "Explico", {})
                self.assertEqual(result.status, "unavailable")
                self.assertIsNone(result.score)
                self.assertIsNone(result.bad_explanation)

    async def test_invalid_output_is_not_accepted(self):
        invalid_outputs = [
            {"score": 6, "reasoning": "Fuera de rango", "knowledge_gaps": []},
            {"score": "4", "reasoning": "Tipo incorrecto", "knowledge_gaps": []},
            {"score": True, "reasoning": "No es un entero", "knowledge_gaps": []},
            {"score": 4, "reasoning": "  ", "knowledge_gaps": []},
            {"score": 2, "reasoning": "Brecha vacía", "knowledge_gaps": [""]},
        ]
        for output in invalid_outputs:
            with self.subTest(output=output):
                client = fake_client(SimpleNamespace(stop_reason="end_turn", parsed_output=output))
                with patch("services.llm_judge.AsyncAnthropic", return_value=client):
                    result = await CoherenceJudge(Settings(anthropic_api_key="test")).evaluate("x=1", "Explico", {})
                self.assertEqual(result.status, "unavailable")

    async def test_provider_error_does_not_leak_details(self):
        error = APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
        client = fake_client(error=error)
        with patch("services.llm_judge.AsyncAnthropic", return_value=client):
            result = await CoherenceJudge(Settings(anthropic_api_key="secret-test-key")).evaluate("private-code", "private-text", {})
        self.assertEqual(result.status, "unavailable")
        for value in ["secret-test-key", "private-code", "private-text"]:
            self.assertNotIn(value, result.model_dump_json())

    async def test_real_sdk_parses_structured_response_with_mock_transport(self):
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx2.Response(200, json={
                "id": "msg_test", "type": "message", "role": "assistant",
                "model": "claude-sonnet-4-6", "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "content": [{"type": "text", "text": json.dumps({
                    "score": 4, "reasoning": "Explica la asignación correctamente.", "knowledge_gaps": []
                })}],
            })

        client = AsyncAnthropic(
            api_key="test", max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )
        with patch("services.llm_judge.AsyncAnthropic", return_value=client):
            result = await CoherenceJudge(Settings(anthropic_api_key="test")).evaluate("x=1", "Asigno uno a x", {})
        self.assertEqual(result.score, 4)
        self.assertEqual(result.status, "evaluated")
        self.assertEqual(requests[0]["output_config"]["format"]["type"], "json_schema")
