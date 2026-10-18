import sqlite3
import unittest
from pathlib import Path

from services.evaluation_repository import SQLiteEvaluationRepository
from tests.evaluation_contract import EvaluationRepositoryContract
from tests.helpers import TemporaryTestDirectory


class SQLiteEvaluationTests(EvaluationRepositoryContract, unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryTestDirectory()
        self.path = Path(self.temp.name) / "sessions.sqlite3"
        self.repository = SQLiteEvaluationRepository(self.path)
        self.uid = "user-one"
        self.other_uid = "user-two"

    def tearDown(self):
        self.temp.cleanup()

    def test_session_survives_reopening_repository(self):
        session = self.session()
        self.repository.save(session)
        reopened = SQLiteEvaluationRepository(self.path)
        self.assertEqual(reopened.find(self.uid, session.session_id), session)

    def test_failed_write_does_not_leave_a_partial_session(self):
        with self.repository.connection() as connection:
            connection.execute("CREATE TRIGGER fail_write BEFORE INSERT ON evaluation_sessions BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        session = self.session()
        with self.assertRaises(sqlite3.DatabaseError):
            self.repository.save(session)
        self.assertIsNone(self.repository.find(self.uid, session.session_id))
        self.assertEqual(self.repository.page(self.uid, 20, None).sessions, [])
