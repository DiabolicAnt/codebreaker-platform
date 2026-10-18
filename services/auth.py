"""La identidad proviene exclusivamente de un ID token verificado por Firebase."""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth, exceptions
from google.auth.exceptions import GoogleAuthError
from pydantic import BaseModel, Field, ValidationError
from requests.exceptions import RequestException

from config import get_settings
from services.firebase import FirebaseConfigurationError, get_firebase_app


logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False, description="ID token obtenido al iniciar sesión con Firebase")


class AuthenticatedUser(BaseModel):
    uid: str = Field(min_length=1, max_length=128)


def unauthenticated() -> HTTPException:
    return HTTPException(status_code=401, detail="Inicia sesión con Firebase y envía un ID token válido",
                         headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    credential: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedUser:
    if credential is None:
        raise unauthenticated()
    try:
        app = get_firebase_app()
        claims = auth.verify_id_token(
            credential.credentials, app=app, check_revoked=get_settings().firebase_check_revoked,
        )
        return AuthenticatedUser(uid=claims["uid"])
    except (auth.InvalidIdTokenError, auth.RevokedIdTokenError, auth.UserDisabledError,
            auth.UserNotFoundError, exceptions.InvalidArgumentError, ValidationError, KeyError) as exc:
        raise unauthenticated() from exc
    except (FirebaseConfigurationError, auth.CertificateFetchError, exceptions.FirebaseError,
            GoogleAuthError, RequestException, OSError) as exc:
        logger.warning("Autenticación no disponible (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="La autenticación no está disponible") from exc


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def require_owner(user: AuthenticatedUser, claimed_user_id: str | None) -> str:
    # Compatibilidad con clientes anteriores: el identificador ya no es una autoridad.
    if claimed_user_id is not None and claimed_user_id != user.uid:
        raise HTTPException(status_code=403, detail="No puedes acceder a datos de otro usuario")
    return user.uid
