from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from services.complexity import (
    CodeValidationError, ComplexityReport, analyze_code, normalize_language,
)
from services.llm_judge import CoherenceJudge, CoherenceResult, get_judge


router = APIRouter(tags=["evaluator"])


class CodeSubmission(BaseModel):
    code: str = Field(max_length=40_000)
    language: str = "python"
    explanation: str = Field(default="", max_length=20_000)

    @field_validator("language")
    @classmethod
    def supported_language(cls, value: str) -> str:
        return normalize_language(value)


class EvaluationResult(BaseModel):
    complexity: ComplexityReport
    # null significa que no hubo evaluación de la explicación.
    bad_explanation_flag: bool | None
    feedback: str
    coherence: CoherenceResult


@router.post("/analyze", response_model=EvaluationResult)
async def analyze(submission: CodeSubmission, judge: CoherenceJudge = Depends(get_judge)):
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
    return EvaluationResult(
        complexity=report,
        bad_explanation_flag=coherence.bad_explanation,
        feedback=feedback,
        coherence=coherence,
    )
