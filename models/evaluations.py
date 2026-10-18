"""Contratos de evaluación e historial; la identidad y las fechas son del servidor."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from models.flashcards import UserId
from services.complexity import ComplexityReport, RiskLevel, normalize_language
from services.llm_judge import CoherenceResult


class EvaluationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=40_000)
    language: str = "python"
    explanation: str = Field(default="", max_length=20_000)

    @field_validator("language")
    @classmethod
    def supported_language(cls, value: str) -> str:
        return normalize_language(value)


class CodeSubmission(EvaluationInput):
    session_id: UUID | None = None

    def content(self) -> EvaluationInput:
        return EvaluationInput(**self.model_dump(exclude={"session_id"}))


class EvaluationResult(BaseModel):
    session_id: UUID
    created_at: AwareDatetime
    complexity: ComplexityReport
    bad_explanation_flag: bool | None
    feedback: str
    coherence: CoherenceResult


class EvaluationSession(EvaluationResult):
    user_id: UserId
    submission: EvaluationInput

    def result(self) -> EvaluationResult:
        return EvaluationResult.model_validate(self.model_dump(exclude={"user_id", "submission"}))


class EvaluationSummary(BaseModel):
    session_id: UUID
    created_at: AwareDatetime
    language: str
    coherence_status: Literal["evaluated", "insufficient_evidence", "unavailable"]
    score: int | None
    bad_explanation_flag: bool | None
    knowledge_gaps: list[str]
    risk_level: RiskLevel
    max_cyclomatic: int

    @classmethod
    def from_session(cls, session: EvaluationSession):
        return cls(
            session_id=session.session_id, created_at=session.created_at,
            language=session.submission.language, coherence_status=session.coherence.status,
            score=session.coherence.score, bad_explanation_flag=session.bad_explanation_flag,
            knowledge_gaps=session.coherence.knowledge_gaps, risk_level=session.complexity.risk_level,
            max_cyclomatic=session.complexity.max_cyclomatic,
        )


class EvaluationPage(BaseModel):
    sessions: list[EvaluationSummary]
    next_cursor: UUID | None
