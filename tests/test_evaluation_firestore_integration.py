"""Contrato de sesiones contra Firestore optativo, con datos propios y limpieza."""

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from google.cloud.firestore_v1.transaction import Transaction

from services.firebase import get_firestore_client
from services.firestore_evaluations import FirestoreEvaluationRepository
from tests.evaluation_contract import EvaluationRepositoryContract


@unittest.skipUnless(os.getenv("FIRESTORE_INTEGRATION_TESTS") == "1", "Firestore externo: habilitación explícita requerida")
class FirestoreEvaluationTests(EvaluationRepositoryContract, unittest.TestCase):
    def setUp(self):
        self.repository = FirestoreEvaluationRepository(
            get_firestore_client(), root_collection="codebreaker_integration_tests",
        )
        self.uid = "sessions-" + str(uuid4())
        self.other_uid = "sessions-" + str(uuid4())
        self.addCleanup(self.clean_up)

    def clean_up(self):
        for uid in (self.uid, self.other_uid):
            collection = self.repository.collection(uid)
            if not collection.parent.path.startswith("codebreaker_integration_tests/") or not uid.startswith("sessions-"):
                raise RuntimeError("Limpieza fuera del espacio temporal de sesiones")
            for document in collection.stream():
                document.reference.delete()

    def test_session_survives_new_repository_instance(self):
        session = self.session()
        self.repository.save(session)
        reopened = FirestoreEvaluationRepository(get_firestore_client(), root_collection="codebreaker_integration_tests")
        self.assertEqual(reopened.find(self.uid, session.session_id), session)

    def test_failure_before_commit_does_not_create_session(self):
        session = self.session()
        with patch.object(Transaction, "create", side_effect=RuntimeError("simulated write failure")):
            with self.assertRaises(RuntimeError):
                self.repository.save(session)
        self.assertIsNone(self.repository.find(self.uid, session.session_id))
        self.assertEqual(self.repository.page(self.uid, 20, None).sessions, [])
