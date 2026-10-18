import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2
from anthropic import APIConnectionError, AsyncAnthropic

from config import Settings
from models.flashcards import DraftCard
from services.flashcard_generator import FlashcardGenerator, GeneratedCards, GeneratorUnavailableError


def client_for(output=None, reason="end_turn", error=None):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.messages.parse = AsyncMock(
        return_value=SimpleNamespace(stop_reason=reason, parsed_output=output), side_effect=error
    )
    return client


class GeneratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_gaps_do_not_call_anthropic(self):
        with patch("services.flashcard_generator.AsyncAnthropic") as factory:
            cards = await FlashcardGenerator(Settings()).generate([])
        self.assertEqual(cards, [])
        factory.assert_not_called()

    async def test_missing_key_is_explicit(self):
        with patch("services.flashcard_generator.AsyncAnthropic") as factory:
            with self.assertRaises(GeneratorUnavailableError):
                await FlashcardGenerator(Settings()).generate(["Bucles"])
        factory.assert_not_called()

    async def test_generated_cards_are_ordered_by_input_concepts(self):
        drafts = [
            DraftCard(concept="recursión", question="¿Qué necesita?", answer="Un caso base."),
            DraftCard(concept="bucles", question="¿Qué hacen?", answer="Repiten una operación."),
        ]
        client = client_for(GeneratedCards(cards=drafts))
        with patch("services.flashcard_generator.AsyncAnthropic", return_value=client):
            cards = await FlashcardGenerator(Settings(anthropic_api_key="test")).generate(["Bucles", "Recursión"], "Contexto técnico")
        self.assertEqual([card.concept for card in cards], ["Bucles", "Recursión"])
        request = client.messages.parse.call_args.kwargs
        self.assertEqual(json.loads(request["messages"][0]["content"])["context"], "Contexto técnico")
        self.assertIn("DATOS NO CONFIABLES", request["system"])
        client.__aexit__.assert_awaited_once()

    async def test_incomplete_refused_or_unrelated_output_is_rejected(self):
        draft = DraftCard(concept="Bucles", question="¿Qué hacen?", answer="Repiten una operación.")
        cases = [
            (None, "max_tokens"), (None, "refusal"), (None, "end_turn"),
            (GeneratedCards(cards=[]), "end_turn"),
            (GeneratedCards(cards=[draft, draft]), "end_turn"),
            (GeneratedCards(cards=[draft.model_copy(update={"concept": "Otro"})]), "end_turn"),
            ({"cards": [{"concept": "Bucles", "question": " ", "answer": "Respuesta"}]}, "end_turn"),
        ]
        for output, reason in cases:
            with self.subTest(reason=reason):
                with patch("services.flashcard_generator.AsyncAnthropic", return_value=client_for(output, reason)):
                    with self.assertRaises(GeneratorUnavailableError):
                        await FlashcardGenerator(Settings(anthropic_api_key="test")).generate(["Bucles"])

    async def test_provider_failure_does_not_expose_private_data(self):
        error = APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
        with patch("services.flashcard_generator.AsyncAnthropic", return_value=client_for(error=error)):
            with self.assertRaises(GeneratorUnavailableError) as caught:
                await FlashcardGenerator(Settings(anthropic_api_key="private-key")).generate(["Bucles"], "private-context")
        self.assertNotIn("private-key", str(caught.exception))
        self.assertNotIn("private-context", str(caught.exception))

    async def test_real_sdk_parses_generated_cards_with_mock_transport(self):
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx2.Response(200, json={
                "id": "msg_cards", "type": "message", "role": "assistant",
                "model": "claude-sonnet-4-6", "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "content": [{"type": "text", "text": json.dumps({"cards": [{
                    "concept": "Bucles", "question": "¿Qué hace un bucle?", "answer": "Repite una operación."
                }]})}],
            })

        client = AsyncAnthropic(api_key="test", max_retries=0, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        with patch("services.flashcard_generator.AsyncAnthropic", return_value=client):
            cards = await FlashcardGenerator(Settings(anthropic_api_key="test")).generate(["Bucles"])
        self.assertEqual(cards[0].concept, "Bucles")
        self.assertEqual(requests[0]["output_config"]["format"]["type"], "json_schema")
