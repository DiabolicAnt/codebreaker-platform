import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from firebase_admin import auth
from requests.exceptions import ConnectionError

from config import Settings
from main import app
from services.firebase import FirebaseConfigurationError
from services.flashcards import get_flashcards_service
from services.evaluations import get_evaluations_service


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.app_patch = patch("services.auth.get_firebase_app", return_value=Mock())
        self.app_patch.start()
        self.settings_patch = patch("services.auth.get_settings", return_value=Settings())
        self.settings_patch.start()

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.settings_patch.stop()
        self.app_patch.stop()

    def test_no_credentials_or_wrong_scheme_rejected_before_storage_or_judge(self):
        service = Mock()
        app.dependency_overrides[get_flashcards_service] = service
        app.dependency_overrides[get_evaluations_service] = service
        cases = [("GET", "/auth/me", None), ("GET", "/flashcards/due", None),
                 ("GET", "/flashcards/due/caleb", None),
                 ("GET", "/flashcards/00000000-0000-0000-0000-000000000001", None),
                 ("GET", "/flashcards/caleb/00000000-0000-0000-0000-000000000001", None),
                 ("POST", "/flashcards/", {"cards": []}),
                 ("POST", "/flashcards/review", {}),
                 ("POST", "/flashcards/generate", {"knowledge_gaps": []}),
                 ("POST", "/evaluator/analyze", {"code": "x=1"}),
                 ("GET", "/evaluator/sessions", None),
                 ("GET", "/evaluator/sessions/00000000-0000-0000-0000-000000000001", None)]
        for method, path, body in cases:
            for headers in ({}, {"Authorization": "Basic invalid"}):
                with self.subTest(path=path, headers=headers):
                    response = self.client.request(method, path, json=body, headers=headers)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.headers["www-authenticate"], "Bearer")
        service.assert_not_called()

    def test_verified_uid_is_returned_and_revocation_is_checked(self):
        with patch("services.auth.auth.verify_id_token", return_value={"uid": "firebase-user", "email": "private@example.test"}) as verify:
            response = self.client.get("/auth/me", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"uid": "firebase-user"})
        self.assertEqual(verify.call_args.args, ("test-token",))
        self.assertTrue(verify.call_args.kwargs["check_revoked"])

    def test_invalid_expired_revoked_disabled_and_wrong_project_tokens_are_401(self):
        errors = [auth.InvalidIdTokenError("wrong project"), auth.ExpiredIdTokenError("expired", None),
                  auth.RevokedIdTokenError("revoked"), auth.UserDisabledError("disabled"),
                  auth.UserNotFoundError("deleted")]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with patch("services.auth.auth.verify_id_token", side_effect=error):
                    response = self.client.get("/auth/me", headers={"Authorization": "Bearer private-token"})
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("private-token", response.text)

    def test_auth_provider_failures_and_missing_configuration_are_503(self):
        for error in [auth.CertificateFetchError("private-detail", None), ConnectionError("private-detail")]:
            with patch("services.auth.auth.verify_id_token", side_effect=error):
                response = self.client.get("/auth/me", headers={"Authorization": "Bearer test"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private-detail", response.text)
        with patch("services.auth.get_firebase_app", side_effect=FirebaseConfigurationError("private-path")):
            response = self.client.get("/auth/me", headers={"Authorization": "Bearer test"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private-path", response.text)

    def test_health_docs_remain_public_and_openapi_requires_bearer(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/docs").status_code, 200)
        schema = self.client.get("/openapi.json").json()
        self.assertIn("HTTPBearer", schema["components"]["securitySchemes"])
        for path in ["/auth/me", "/evaluator/analyze", "/evaluator/sessions", "/evaluator/sessions/{session_id}", "/flashcards/due"]:
            operation = next(iter(schema["paths"][path].values()))
            self.assertEqual(operation["security"], [{"HTTPBearer": []}])
