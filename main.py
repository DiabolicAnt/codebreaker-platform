from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from routers import auth, evaluator, flashcards


app = FastAPI(title="Code-Breaker-API", version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(evaluator.router, prefix="/evaluator")
app.include_router(flashcards.router, prefix="/flashcards")
app.include_router(auth.router, prefix="/auth")


@app.get("/")
def root():
    return {"status": "ok", "service": "CodeBreaker API"}
