"""Garantías compartidas por SQLite y Firestore, con lógica de dominio real."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from models.evaluations import EvaluationInput, EvaluationSession
from services.complexity import analyze_code
from services.evaluation_repository import SessionConflictError, SessionNotFoundError
from services.llm_judge import CoherenceResult


class EvaluationRepositoryContract:
    def session(self, *, session_id=None, user_id=None, code="x=1", at=None):
        return EvaluationSession(
            session_id=session_id or uuid4(), user_id=user_id or self.uid,
            created_at=at or datetime(2026, 10, 18, 18, tzinfo=timezone.utc),
            submission=EvaluationInput(code=code, explanation="Asigno un valor."),
            complexity=analyze_code(code), feedback="Resultado de prueba", bad_explanation_flag=False,
            coherence=CoherenceResult(status="evaluated", score=4, reasoning="Coincide.",
                                      bad_explanation=False, knowledge_gaps=[]),
        )

    def test_complete_session_round_trip_and_empty_history(self):
        self.assertEqual(self.repository.page(self.uid, 20, None).sessions, [])
        self.assertIsNone(self.repository.find(self.uid, uuid4()))
        session = self.session()
        self.assertEqual(self.repository.save(session), session)
        self.assertEqual(self.repository.find(self.uid, session.session_id), session)
        page = self.repository.page(self.uid, 20, None)
        self.assertEqual([s.session_id for s in page.sessions], [session.session_id])
        self.assertIsNone(page.next_cursor)

    def test_retry_preserves_original_result_and_timestamp(self):
        session = self.session()
        self.repository.save(session)
        changed_result = session.model_copy(update={"feedback": "Otro resultado", "created_at": session.created_at + timedelta(days=1)})
        self.assertEqual(self.repository.save(changed_result), session)
        self.assertEqual(len(self.repository.page(self.uid, 100, None).sessions), 1)

    def test_changed_submission_for_same_id_conflicts(self):
        session = self.session()
        self.repository.save(session)
        changed = session.model_copy(update={"submission": EvaluationInput(code="x=2", explanation="Otro")})
        with self.assertRaises(SessionConflictError):
            self.repository.save(changed)
        self.assertEqual(self.repository.find(self.uid, session.session_id), session)

    def test_user_isolation_including_same_uuid(self):
        session = self.session()
        self.repository.save(session)
        self.assertIsNone(self.repository.find(self.other_uid, session.session_id))
        self.assertEqual(self.repository.page(self.other_uid, 20, None).sessions, [])
        with self.assertRaises(SessionNotFoundError):
            self.repository.page(self.other_uid, 20, session.session_id)
        other = self.session(session_id=session.session_id, user_id=self.other_uid, code="x=2")
        self.repository.save(other)
        self.assertEqual(self.repository.find(self.uid, session.session_id), session)
        self.assertEqual(self.repository.find(self.other_uid, session.session_id), other)

    def test_pagination_ties_do_not_skip_or_duplicate_sessions(self):
        sessions = [self.session(session_id=UUID(int=value)) for value in range(1, 6)]
        for session in sessions:
            self.repository.save(session)
        page1 = self.repository.page(self.uid, 2, None)
        self.assertEqual([s.session_id for s in page1.sessions], [UUID(int=5), UUID(int=4)])
        # Una sesión nueva entre páginas no desplaza los elementos anteriores.
        self.repository.save(self.session(at=sessions[0].created_at + timedelta(days=1)))
        page2 = self.repository.page(self.uid, 2, page1.next_cursor)
        page3 = self.repository.page(self.uid, 2, page2.next_cursor)
        self.assertEqual([s.session_id for s in page2.sessions], [UUID(int=3), UUID(int=2)])
        self.assertEqual([s.session_id for s in page3.sessions], [UUID(int=1)])
        self.assertIsNone(page3.next_cursor)

    def test_unknown_cursor_does_not_restart_at_first_page(self):
        self.repository.save(self.session())
        with self.assertRaises(SessionNotFoundError):
            self.repository.page(self.uid, 20, uuid4())

    def test_concurrent_identical_saves_preserve_one_session(self):
        session = self.session()
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.repository.save(session), range(3)))
        self.assertEqual(results, [session] * 3)
        self.assertEqual(len(self.repository.page(self.uid, 100, None).sessions), 1)

    def test_concurrent_different_payloads_allow_only_one(self):
        session = self.session()
        different = self.session(session_id=session.session_id, code="x=2")

        def attempt(value):
            try:
                return self.repository.save(value)
            except SessionConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [session, different]))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(len(self.repository.page(self.uid, 100, None).sessions), 1)

    def test_unavailable_assessment_is_not_converted_into_good_score(self):
        session = self.session().model_copy(update={
            "coherence": CoherenceResult(status="unavailable", reasoning="Proveedor no disponible"),
            "bad_explanation_flag": None,
        })
        self.repository.save(session)
        stored = self.repository.find(self.uid, session.session_id)
        summary = self.repository.page(self.uid, 20, None).sessions[0]
        self.assertIsNone(stored.coherence.score)
        self.assertIsNone(summary.bad_explanation_flag)
        self.assertEqual(summary.coherence_status, "unavailable")
