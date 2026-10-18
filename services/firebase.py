"""Clientes Firebase inicializados bajo demanda; las credenciales quedan en el servidor."""

import os
from functools import lru_cache
from threading import Lock

import firebase_admin
from firebase_admin import credentials, firestore
from google.auth.exceptions import DefaultCredentialsError

from config import get_settings


class FirebaseConfigurationError(RuntimeError):
    pass


_initialization_lock = Lock()


@lru_cache
def get_firebase_app():
    settings = get_settings()
    if not settings.firebase_project_id:
        raise FirebaseConfigurationError("Falta configurar Firebase")
    if not settings.firebase_use_emulators and any(
        os.getenv(name) for name in ("FIREBASE_AUTH_EMULATOR_HOST", "FIRESTORE_EMULATOR_HOST")
    ):
        raise FirebaseConfigurationError("Los emuladores requieren habilitación explícita")
    try:
        if settings.google_application_credentials:
            credential = credentials.Certificate(str(settings.google_application_credentials))
            if credential.project_id != settings.firebase_project_id:
                raise FirebaseConfigurationError("La credencial pertenece a otro proyecto")
        else:
            credential = credentials.ApplicationDefault()
        # lru_cache puede ejecutar su función dos veces en el primer acceso concurrente.
        with _initialization_lock:
            try:
                app = firebase_admin.get_app("codebreaker-api")
            except ValueError:
                app = firebase_admin.initialize_app(
                    credential,
                    options={"projectId": settings.firebase_project_id,
                             "httpTimeout": settings.firebase_timeout_seconds},
                    name="codebreaker-api",
                )
            if app.project_id != settings.firebase_project_id:
                raise FirebaseConfigurationError("La app pertenece a otro proyecto")
            return app
    except (ValueError, OSError, DefaultCredentialsError) as exc:
        raise FirebaseConfigurationError("No se pudo configurar Firebase") from exc


@lru_cache
def get_firestore_client():
    return firestore.client(app=get_firebase_app(), database_id=get_settings().firestore_database_id)
