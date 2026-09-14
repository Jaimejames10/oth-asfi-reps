"""Persistencia del estado de alertas compatible con el JSON existente."""

from __future__ import annotations

import json
from pathlib import Path


def cargar_estado(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            pass
    return {"alertas_enviadas": {}, "ultima_revision": None}


def guardar_estado(path: Path, estado: dict) -> None:
    path.write_text(
        json.dumps(estado, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def clave_reporte(reporte: dict) -> str:
    return f"{reporte['fecha_corte']}|{reporte['grupo']}|{reporte['envio']}"
