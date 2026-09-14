"""Conexion SQLite, rutas de datos y reloj local del monitor."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIMEZONE = "America/La_Paz"
try:
    LOCAL_TIMEZONE = ZoneInfo(DEFAULT_TIMEZONE) if ZoneInfo else timezone(
        timedelta(hours=-4), DEFAULT_TIMEZONE
    )
except Exception:  # Windows puede no tener tzdata instalado
    LOCAL_TIMEZONE = timezone(timedelta(hours=-4), DEFAULT_TIMEZONE)


def resolve_path(path: str | Path) -> Path:
    """Resuelve paths relativos al directorio del proyecto."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def local_now() -> datetime:
    """Obtiene la hora local de Bolivia."""
    return datetime.now(LOCAL_TIMEZONE) if LOCAL_TIMEZONE else datetime.now()


def now_iso(value: datetime | None = None) -> str:
    value = value or local_now()
    return value.isoformat(timespec="seconds")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Abre SQLite con las mismas politicas WAL usadas por el monitor."""
    path = resolve_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn
