from dataclasses import asdict
import logging
import sqlite3
from typing import Annotated, Callable, TypeVar
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from services.complexity import CodeValidationError, analyze_code
from services.llm_judge import CoherenceJudge, get_judge
from services.auth import CurrentUser
from models.evaluations import CodeSubmission, EvaluationResult, EvaluationSession, EvaluationPage
from services.evaluation_repository import EvaluationStorageError, SessionConflictError, SessionNotFoundError
from services.evaluations import EvaluationsService, get_evaluations_service


router = APIRouter(tags=["evaluator"])
logger = logging.getLogger(__name__)
Result = TypeVar("Result")


def history_operation(operation: Callable[[], Result]) -> Result:
    try:
        return operation()
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SessionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (EvaluationStorageError, OSError, sqlite3.DatabaseError) as exc:
        logger.warning("Historial no disponible (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="El historial de evaluaciones no está disponible") from exc


@router.post("/analyze", response_model=EvaluationResult)
async def analyze(
    submission: CodeSubmission, user: CurrentUser,
    judge: CoherenceJudge = Depends(get_judge),
    sessions: EvaluationsService = Depends(get_evaluations_service),
):
    session_id = submission.session_id or uuid4()
    content = submission.content()
    existing = await run_in_threadpool(
        history_operation, lambda: sessions.existing(user.uid, session_id, content),
    )
    if existing is not None:
        return existing
    try:
        report = await run_in_threadpool(analyze_code, submission.code, submission.language)
    except CodeValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    coherence = await judge.evaluate(
        submission.code, submission.explanation, asdict(report), submission.language
    )
    feedback = (
        f"Complejidad ciclomática promedio: {report.cyclomatic}; "
        f"máxima: {report.max_cyclomatic} ({report.risk_level}). {coherence.reasoning}"
    )
    return await run_in_threadpool(history_operation, lambda: sessions.save(
        user.uid, content, session_id=session_id, complexity=report,
        bad_explanation_flag=coherence.bad_explanation, feedback=feedback, coherence=coherence,
    ))


@router.get("/sessions", response_model=EvaluationPage)
def list_sessions(
    user: CurrentUser, sessions: EvaluationsService = Depends(get_evaluations_service),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: UUID | None = None,
):
    return history_operation(lambda: sessions.page(user.uid, limit, cursor))


@router.get("/sessions/{session_id}", response_model=EvaluationSession)
def get_session(
    session_id: UUID, user: CurrentUser,
    sessions: EvaluationsService = Depends(get_evaluations_service),
):
    return history_operation(lambda: sessions.get(user.uid, session_id))
