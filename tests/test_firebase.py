import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from google.api_core.exceptions import PermissionDenied, ServiceUnavailable

from config import Settings
from services import firebase
from services.flashcards import get_flashcards_service
from services.firestore_repository import FirestoreFlashcardRepository
from services.flashcard_repository import StorageUnavailableError


class FirebaseConfigurationTests(unittest.TestCase):
    def setUp(self):
        firebase.get_firebase_app.cache_clear()
        firebase.get_firestore_client.cache_clear()
        get_flashcards_service.cache_clear()

    def tearDown(self):
        firebase.get_firebase_app.cache_clear()
        firebase.get_firestore_client.cache_clear()
        get_flashcards_service.cache_clear()

    def test_missing_project_is_rejected_before_credentials_are_loaded(self):
        with patch.object(firebase, "get_settings", return_value=Settings()), patch.object(firebase.credentials, "Certificate") as certificate:
            with self.assertRaises(firebase.FirebaseConfigurationError):
                firebase.get_firebase_app()
        certificate.assert_not_called()

    def test_certificate_must_match_project(self):
        settings = Settings(firebase_project_id="expected", google_application_credentials=Path("private.json"))
        with patch.object(firebase, "get_settings", return_value=settings), patch.object(firebase.credentials, "Certificate", return_value=Mock(project_id="other")), patch.object(firebase.firebase_admin, "initialize_app") as initialize:
            with self.assertRaises(firebase.FirebaseConfigurationError):
                firebase.get_firebase_app()
        initialize.assert_not_called()

    def test_emulator_cannot_be_enabled_accidentally(self):
        with patch.object(firebase, "get_settings", return_value=Settings(firebase_project_id="expected")), patch.dict("os.environ", {"FIREBASE_AUTH_EMULATOR_HOST": "localhost:9099"}):
            with self.assertRaises(firebase.FirebaseConfigurationError):
                firebase.get_firebase_app()

    def test_application_default_and_named_database_are_supported(self):
        settings = Settings(firebase_project_id="expected", firestore_database_id="other-db")
        application = Mock(project_id="expected")
        with patch.object(firebase, "get_settings", return_value=settings), patch.object(firebase.credentials, "ApplicationDefault"), patch.object(firebase.firebase_admin, "get_app", side_effect=ValueError()), patch.object(firebase.firebase_admin, "initialize_app", return_value=application) as initialize, patch.object(firebase.firestore, "client") as client:
            firebase.get_firestore_client()
            firebase.get_firestore_client()
        initialize.assert_called_once()
        self.assertEqual(initialize.call_args.kwargs["options"]["projectId"], "expected")
        client.assert_called_once_with(app=application, database_id="other-db")

    def test_firestore_factory_never_falls_back_to_sqlite_on_failure(self):
        settings = Settings(flashcards_backend="firestore", firebase_project_id="expected")
        with patch("services.flashcards.get_settings", return_value=settings), patch("services.flashcards.get_firestore_client", side_effect=firebase.FirebaseConfigurationError("private.json")):
            with self.assertRaises(HTTPException) as error:
                get_flashcards_service()
        self.assertEqual(error.exception.status_code, 503)
        self.assertNotIn("private.json", error.exception.detail)

    def test_firestore_permission_or_connection_errors_are_wrapped(self):
        client = Mock()
        repository = FirestoreFlashcardRepository(client)
        for error in [PermissionDenied("private"), ServiceUnavailable("private")]:
            client.collection.return_value.document.return_value.collection.return_value.document.return_value.get.side_effect = error
            with self.assertRaises(StorageUnavailableError):
                repository.get("owner", "card")
