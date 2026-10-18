"""Rutas de creación, generación, consulta y revisión de flashcards."""

import logging
import sqlite3
from typing import Annotated, Callable, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from models.flashcards import (
    CreateFlashcardsRequest, DueFlashcards, Flashcard, FlashcardsBatch, GenerateFlashcardsRequest,
    ReviewFlashcardBody, ReviewFlashcardRequest, ReviewResult, UserId,
)
from services.flashcard_generator import (
    FlashcardGenerator, GeneratorUnavailableError, get_flashcard_generator,
)
from services.flashcard_repository import CardNotFoundError, ReviewConflictError, StorageUnavailableError
from services.flashcards import FlashcardsService, get_flashcards_service
from services.fsrs_engine import ReviewTimeError
from services.auth import CurrentUser, require_owner


router = APIRouter(tags=["flashcards"])
logger = logging.getLogger(__name__)
Result = TypeVar("Result")


def storage_operation(operation: Callable[[], Result]) -> Result:
    try:
        return operation()
    except CardNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ReviewConflictError, ReviewTimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (sqlite3.DatabaseError, OSError, StorageUnavailableError) as exc:
        logger.warning("Almacenamiento de flashcards no disponible (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="El almacenamiento de flashcards no está disponible") from exc


@router.post("/", response_model=FlashcardsBatch)
def create_flashcards(
    request: CreateFlashcardsRequest, user: CurrentUser,
    service: FlashcardsService = Depends(get_flashcards_service)
):
    uid = require_owner(user, request.user_id)
    return storage_operation(lambda: service.create(uid, request.cards))


@router.post("/generate", response_model=FlashcardsBatch)
async def generate_flashcards(
    request: GenerateFlashcardsRequest,
    user: CurrentUser,
    service: FlashcardsService = Depends(get_flashcards_service),
    generator: FlashcardGenerator = Depends(get_flashcard_generator),
):
    uid = require_owner(user, request.user_id)
    try:
        drafts = await generator.generate(request.knowledge_gaps, request.context)
    except GeneratorUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return await run_in_threadpool(storage_operation, lambda: service.create(uid, drafts))


@router.post("/review", response_model=ReviewResult)
def review_flashcard(
    request: ReviewFlashcardBody, user: CurrentUser,
    service: FlashcardsService = Depends(get_flashcards_service)
):
    uid = require_owner(user, request.user_id)
    command = ReviewFlashcardRequest(**dict(request.model_dump(), user_id=uid))
    return storage_operation(lambda: service.review(command))


@router.get("/due", response_model=DueFlashcards)
def my_due_flashcards(
    user: CurrentUser, service: FlashcardsService = Depends(get_flashcards_service),
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    return storage_operation(lambda: service.due(user.uid, limit))


@router.get("/due/{user_id}", response_model=DueFlashcards, deprecated=True)
def due_flashcards(
    user_id: UserId,
    user: CurrentUser,
    service: FlashcardsService = Depends(get_flashcards_service),
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    uid = require_owner(user, user_id)
    return storage_operation(lambda: service.due(uid, limit))


@router.get("/{card_id}", response_model=Flashcard)
def my_flashcard(
    card_id: UUID, user: CurrentUser,
    service: FlashcardsService = Depends(get_flashcards_service),
):
    return storage_operation(lambda: service.get(user.uid, card_id))


@router.get("/{user_id}/{card_id}", response_model=Flashcard, deprecated=True)
def get_flashcard(
    user_id: UserId, card_id: UUID, user: CurrentUser,
    service: FlashcardsService = Depends(get_flashcards_service),
):
    uid = require_owner(user, user_id)
    return storage_operation(lambda: service.get(uid, card_id))
