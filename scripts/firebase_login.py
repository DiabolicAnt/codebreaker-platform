"""Obtiene un ID token con correo/contraseña; sólo el token se escribe a stdout.

Uso: $token = .\venv\Scripts\python.exe .\scripts\firebase_login.py
No es un endpoint del backend ni almacena credenciales.
"""

import getpass
import sys

import requests


def main():
    print("Web API key de Firebase (Configuración del proyecto → General):", file=sys.stderr)
    api_key = input().strip()
    print("Correo del usuario de prueba de Firebase:", file=sys.stderr)
    email = input().strip()
    password = getpass.getpass("Contraseña: ")
    try:
        response = requests.post(
            "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
            params={"key": api_key},
            json={"email": email, "password": password, "returnSecureToken": True}, timeout=15,
        )
        if response.status_code != 200:
            print("No se pudo iniciar sesión. Revisa el usuario, contraseña, proveedor y Web API key.", file=sys.stderr)
            return 1
        print(response.json()["idToken"])
        return 0
    except (requests.RequestException, ValueError, KeyError):
        print("No se pudo obtener el ID token de Firebase.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
