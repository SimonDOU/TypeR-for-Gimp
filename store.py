"""Persistencia del estado en JSON, segura entre procesos.

GIMP lanza cada procedimiento del plugin (panel, atajo «insertar siguiente»,
etc.) en un proceso distinto. El estado compartido (guion, cursor, estilos)
vive por tanto en disco:

* escritura atómica (archivo temporal + os.replace)
* `transaction()` = leer-modificar-escribir bajo flock, así el panel y los
  atajos nunca se pisan el cursor.
"""
from __future__ import annotations

__version__ = "0.4.0"   # debe coincidir con typer_core.__version__

import fcntl
import json
import os
from contextlib import contextmanager

from .models import AppState

STATE_NAME = "state.json"


def default_directory() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "typer-gimp")


class StateStore:
    STATE_NAME = STATE_NAME

    def __init__(self, directory: str | None = None):
        self.directory = directory or default_directory()
        self.path = os.path.join(self.directory, STATE_NAME)
        self._lock_path = os.path.join(self.directory, "state.lock")

    # ---- lectura sin bloqueo (el archivo se reemplaza atómicamente) ------- #
    def load(self) -> AppState:
        return self._read()

    def _read(self) -> AppState:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return AppState.from_dict(json.load(fh))
        except FileNotFoundError:
            return AppState()
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            try:  # conserva el archivo corrupto en lugar de perderlo
                os.replace(self.path, self.path + ".corrupt")
            except OSError:
                pass
            return AppState()

    def _write(self, state: AppState) -> None:
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state.to_dict(), fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---- lectura-modificación-escritura ----------------------------------- #
    @contextmanager
    def transaction(self):
        """Uso:  with store.transaction() as st: st.advance()

        Si dentro del bloque se lanza una excepción NO se guarda nada
        (p. ej. si la inserción falla, el cursor no avanza).
        """
        os.makedirs(self.directory, exist_ok=True)
        with open(self._lock_path, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                state = self._read()
                yield state
                self._write(state)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
