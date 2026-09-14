"""Punto de entrada compatible para la interfaz de administracion."""

from asfi_monitor_app.ui import dashboard as _implementation


def __getattr__(name):
    return getattr(_implementation, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_implementation)))


def ejecutar_gui(db_path=None) -> None:
    _implementation.ejecutar_gui(db_path)


if __name__ == "__main__":
    ejecutar_gui()
