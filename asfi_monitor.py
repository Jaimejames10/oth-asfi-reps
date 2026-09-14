"""Punto de entrada compatible del monitor ASFI/SCIP.

La implementacion vive en ``asfi_monitor_app.application.monitor_service``.
Este archivo se conserva para mantener los comandos, tareas programadas y
imports existentes.
"""

from asfi_monitor_app.application import monitor_service as _implementation
from asfi_monitor_app.application.cli import main as _cli_main


def __getattr__(name):
    return getattr(_implementation, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_implementation)))


def main() -> None:
    _cli_main()


if __name__ == "__main__":
    main()
