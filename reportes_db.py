"""Fachada compatible para la persistencia del monitor ASFI/SCIP."""

from asfi_monitor_app.storage import api as _implementation


def __getattr__(name):
    return getattr(_implementation, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_implementation)))
