"""Validacion y formateo de valores usados por la interfaz Tkinter."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from asfi_monitor_app.storage import api as reportes_db


DIAS_SEMANA = (
    (1, "Lunes"),
    (2, "Martes"),
    (3, "Miércoles"),
    (4, "Jueves"),
    (5, "Viernes"),
    (6, "Sábado"),
    (7, "Domingo"),
)
DIAS_SEMANA_OPCIONES = tuple(f"{numero} - {nombre}" for numero, nombre in DIAS_SEMANA)


def _weekday_label(value: Optional[int], default: int = 5) -> str:
    try:
        number = int(value or default)
    except (TypeError, ValueError):
        number = default
    for weekday, name in DIAS_SEMANA:
        if weekday == number:
            return f"{weekday} - {name}"
    return f"{default} - {dict(DIAS_SEMANA)[default]}"


def _optional_int(value: str, field: str, minimum: int = 0) -> Optional[int]:
    value = value.strip()
    if not value:
        return None
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{field} debe ser un número entero") from exc
    if result < minimum:
        raise ValueError(f"{field} debe ser mayor o igual a {minimum}")
    return result


def _parse_weekday(value: str, field: str = "Día de corte semanal") -> Optional[int]:
    number = value.strip().split("-", 1)[0].strip()
    return _optional_int(number, field, 1)


def _days_to_text(days: list) -> str:
    return ",".join(str(day) for day in days)


def _parse_days(value: str) -> list[int]:
    if not value.strip():
        return []
    try:
        days = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError("Los días deben ser números del 1 al 7 separados por comas") from exc
    if any(day < 1 or day > 7 for day in days):
        raise ValueError("Los días deben estar entre 1 (lunes) y 7 (domingo)")
    return sorted(set(days))


def _display_date(value: Optional[str]) -> str:
    parsed = reportes_db.parse_date(value)
    return reportes_db.format_asfi_date(parsed) if parsed else "-"


def _display_datetime(value: Optional[str]) -> str:
    if not value:
        return "-"
    parsed = reportes_db.parse_datetime(value)
    if parsed:
        return parsed.strftime("%d/%m/%Y %H:%M")
    try:
        return datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(value).replace("T", " ")[:16]


def _overdue_days(row: dict, current: datetime) -> str:
    deadline = reportes_db.parse_datetime(row.get("fecha_hora_limite"))
    if deadline is None:
        return "-"
    if row.get("estado") == "EXITOSO_TARDIO":
        sent = reportes_db.parse_datetime(row.get("fecha_envio"))
        if sent is None:
            return "-"
        return str(max(0, (sent.date() - deadline.date()).days))
    return str(max(0, (current.date() - deadline.date()).days))
