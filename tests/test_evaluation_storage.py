import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from google.api_core.exceptions import PermissionDenied

from config import Settings
from services.evaluation_repository import EvaluationStorageError, SQLiteEvaluationRepository
from services.evaluations import get_evaluations_service
from services.firebase import FirebaseConfigurationError
from services.firestore_evaluations import FirestoreEvaluationRepository
from tests.helpers import TemporaryTestDirectory


class EvaluationStorageTests(unittest.TestCase):
    def setUp(self):
        get_evaluations_service.cache_clear()

    def tearDown(self):
        get_evaluations_service.cache_clear()

    def test_factory_selects_sqlite_with_configured_path(self):
        temp = TemporaryTestDirectory()
        self.addCleanup(temp.cleanup)
        settings = Settings(evaluations_db_path=Path(temp.name) / "history.sqlite3")
        with patch("services.evaluations.get_settings", return_value=settings):
            service = get_evaluations_service()
        self.assertIsInstance(service.repository, SQLiteEvaluationRepository)
        self.assertEqual(service.repository.path, settings.evaluations_db_path)

    def test_factory_selects_firestore_and_does_not_construct_sqlite(self):
        settings = Settings(evaluations_backend="firestore")
        with patch("services.evaluations.get_settings", return_value=settings), patch("services.evaluations.get_firestore_client", return_value=Mock()), patch("services.evaluations.SQLiteEvaluationRepository") as sqlite:
            service = get_evaluations_service()
        self.assertIsInstance(service.repository, FirestoreEvaluationRepository)
        sqlite.assert_not_called()

    def test_firestore_configuration_failure_never_falls_back_to_sqlite(self):
        settings = Settings(evaluations_backend="firestore")
        with patch("services.evaluations.get_settings", return_value=settings), patch("services.evaluations.get_firestore_client", side_effect=FirebaseConfigurationError("private-path")), patch("services.evaluations.SQLiteEvaluationRepository") as sqlite:
            with self.assertRaises(HTTPException) as error:
                get_evaluations_service()
        self.assertEqual(error.exception.status_code, 503)
        self.assertNotIn("private-path", error.exception.detail)
        sqlite.assert_not_called()

    def test_firestore_permission_failures_are_wrapped(self):
        repository = FirestoreEvaluationRepository(Mock())
        with patch.object(repository, "read", side_effect=PermissionDenied("private-detail")):
            with self.assertRaises(EvaluationStorageError):
                repository.find("user", uuid4())
