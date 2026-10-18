"""Carpetas de prueba dentro del proyecto, con eliminación limitada a su raíz."""

import shutil
from pathlib import Path
from uuid import uuid4


class TemporaryTestDirectory:
    def __init__(self):
        self.base = (Path(__file__).resolve().parent.parent / ".test-data").resolve()
        self.path = self.base / str(uuid4())
        self.path.mkdir(parents=True)
        self.name = str(self.path)

    def cleanup(self):
        resolved = self.path.resolve()
        if resolved.parent != self.base:
            raise RuntimeError("La carpeta temporal salió de la raíz de pruebas")
        if resolved.exists():
            shutil.rmtree(resolved)
