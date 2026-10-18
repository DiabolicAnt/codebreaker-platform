from fastapi import APIRouter

from services.auth import AuthenticatedUser, CurrentUser


router = APIRouter(tags=["auth"])


@router.get("/me", response_model=AuthenticatedUser)
def me(user: CurrentUser):
    return user
