"""Persistencia SQLite para el catálogo y el historial del monitor ASFI."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .connection import PROJECT_ROOT, connect, local_now, now_iso, resolve_path
from .schema import SCHEMA_SQL, SCHEMA_VERSION
from .security import _protect_secret, _unprotect_secret

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 sin zoneinfo
    ZoneInfo = None


DEFAULT_TIMEZONE = "America/La_Paz"
WEEKLY_RULE_NAMES = {"SEMANAL", "SEMANA", "VIERNES", "SABADO", "SÁBADO", "DOMINGO"}
WEEKLY_DEADLINE_TIME = "12:00"
MONTHLY_RETIRED_CODES = {
    *(f"MI{number:02d}" for number in range(1, 10)),
    *(f"MB{number:02d}" for number in range(1, 21)),
}
try:
    _LOCAL_TZ = ZoneInfo(DEFAULT_TIMEZONE) if ZoneInfo else timezone(
        timedelta(hours=-4), DEFAULT_TIMEZONE
    )
except Exception:  # Windows puede no tener tzdata instalado
    _LOCAL_TZ = timezone(timedelta(hours=-4), DEFAULT_TIMEZONE)


def _easter_sunday(year: int) -> date:
    """Calcula el Domingo de Pascua para un año gregoriano."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def default_holidays(
    start_year: Optional[int] = None, end_year: Optional[int] = None
) -> list[tuple[date, str]]:
    """Devuelve feriados nacionales bolivianos para un rango de años.

    Los feriados móviles se calculan desde Pascua. Cuando un feriado nacional
    cae domingo, se agrega también el lunes siguiente como día trasladado.
    La tabla sigue siendo editable porque pueden existir disposiciones
    extraordinarias o feriados departamentales.
    """
    current_year = local_now().year
    start_year = current_year - 1 if start_year is None else int(start_year)
    end_year = current_year + 5 if end_year is None else int(end_year)
    holidays: list[tuple[date, str]] = []
    for year in range(start_year, end_year + 1):
        easter = _easter_sunday(year)
        fixed = [
            (date(year, 1, 1), "Año Nuevo"),
            (date(year, 1, 22), "Día del Estado Plurinacional"),
            (date(year, 5, 1), "Día del Trabajo"),
            (date(year, 6, 21), "Año Nuevo Andino Amazónico y Chaqueño"),
            (date(year, 8, 2), "Día de la Revolución Agraria"),
            (date(year, 8, 6), "Día de la Independencia de Bolivia"),
            (date(year, 11, 2), "Día de Todos los Difuntos"),
            (date(year, 12, 25), "Navidad"),
            (easter - timedelta(days=48), "Carnaval"),
            (easter - timedelta(days=47), "Carnaval"),
            (easter - timedelta(days=2), "Viernes Santo"),
            (easter + timedelta(days=60), "Corpus Christi"),
        ]
        for holiday, description in fixed:
            holidays.append((holiday, description))
            if holiday.weekday() == 6:
                holidays.append((holiday + timedelta(days=1), f"{description} (trasladado)"))
    return holidays


def _legacy_now_iso(value: Optional[datetime] = None) -> str:
    value = value or local_now()
    return value.isoformat(timespec="seconds")


def _legacy_connect(db_path: str | Path) -> sqlite3.Connection:
    path = resolve_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_app_setting(
    conn: sqlite3.Connection, key: str, default: Optional[str] = None
) -> Optional[str]:
    """Lee una opción simple de configuración persistida en SQLite."""
    row = conn.execute(
        "SELECT valor FROM app_meta WHERE clave = ?", (key,)
    ).fetchone()
    return row["valor"] if row is not None else default


def set_app_setting(conn: sqlite3.Connection, key: str, value: Optional[str]) -> None:
    """Guarda o elimina una opción simple de configuración."""
    set_app_settings(conn, {key: value})


def set_app_settings(
    conn: sqlite3.Connection, settings: dict[str, Optional[str]]
) -> None:
    """Guarda varias opciones como una sola transacción."""
    with conn:
        for key, value in settings.items():
            if value is None or value == "":
                conn.execute("DELETE FROM app_meta WHERE clave = ?", (key,))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO app_meta(clave, valor) VALUES (?, ?)",
                    (key, value),
                )


def list_holidays(conn: sqlite3.Connection) -> list[dict]:
    """Lista feriados ordenados por fecha."""
    rows = conn.execute(
        "SELECT fecha, descripcion FROM feriados ORDER BY fecha"
    ).fetchall()
    return [dict(row) for row in rows]


def get_holidays(conn: sqlite3.Connection) -> set[date]:
    """Devuelve las fechas configuradas como no hábiles."""
    return {
        parsed
        for row in list_holidays(conn)
        if (parsed := parse_date(row["fecha"])) is not None
    }


def set_holiday(conn: sqlite3.Connection, value: str | date, description: str) -> None:
    """Crea o actualiza un feriado."""
    holiday = parse_date(value)
    if holiday is None:
        raise ValueError("La fecha del feriado no es válida")
    description = str(description or "").strip()
    if not description:
        raise ValueError("La descripción del feriado es obligatoria")
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO feriados(fecha, descripcion) VALUES (?, ?)",
            (holiday.isoformat(), description),
        )


def delete_holiday(conn: sqlite3.Connection, value: str | date) -> bool:
    """Elimina un feriado y devuelve si existía."""
    holiday = parse_date(value)
    if holiday is None:
        return False
    with conn:
        cursor = conn.execute("DELETE FROM feriados WHERE fecha = ?", (holiday.isoformat(),))
    return cursor.rowcount > 0


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except (TypeError, ValueError):
        return []


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = " ".join(text.casefold().split())
    return text


def parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    match = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            pass
    return None


def parse_datetime(value: Any) -> Optional[datetime]:
    """Convierte fechas ASFI con o sin hora a un datetime localizable."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%y %H:%M:%S",
        "%d/%m/%y %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_asfi_date(value: date) -> str:
    return f"{value.day}/{value.month}/{value.year}"


def parse_time(value: Optional[str]) -> Optional[time]:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError as exc:
        raise ValueError("La hora límite debe tener formato HH:MM") from exc


def _combine_date_time(value: date, hour: Optional[str]) -> Optional[datetime]:
    parsed = parse_time(hour)
    if parsed is None:
        return None
    result = datetime.combine(value, parsed)
    return result.replace(tzinfo=_LOCAL_TZ) if _LOCAL_TZ else result


def is_weekly_rule(rule: dict) -> bool:
    """Indica si una regla representa un ciclo semanal configurable."""
    tipo = str(rule.get("tipo_periodo") or "").strip().lower()
    kind = str(rule.get("regla_fecha_corte") or "").strip().upper()
    return tipo == "semanal" or kind in WEEKLY_RULE_NAMES


def _next_monday_after(cutoff: date) -> date:
    """Devuelve el lunes posterior al día de corte, nunca el mismo día."""
    days = (8 - cutoff.isoweekday()) % 7 or 7
    return cutoff + timedelta(days=days)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None and _LOCAL_TZ:
        return value.replace(tzinfo=_LOCAL_TZ)
    if value.tzinfo is not None and _LOCAL_TZ is None:
        return value.replace(tzinfo=None)
    return value


def initialize_database(
    db_path: str | Path,
    seed_path: Optional[str | Path] = None,
    legacy_no_sent_path: Optional[str | Path] = None,
) -> Path:
    """Crea/aplica el esquema y carga la semilla solo si la base está vacía."""
    path = resolve_path(db_path)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA_SQL)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version in {1, 2, 3, 4, 5, 6}:
            _ensure_rule_exception_columns(conn)
            if version == 1:
                try:
                    conn.execute("ALTER TABLE reglas_reportes ADD COLUMN mes_ancla INTEGER")
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
                _migrate_weekly_rules(conn)
                _migrate_monthly_catalog(conn, seed_path)
            elif version == 2:
                _migrate_weekly_rules(conn)
                _migrate_monthly_catalog(conn, seed_path)
            elif version == 3:
                _migrate_monthly_catalog(conn, seed_path)
            elif version == 4:
                _migrate_monthly_catalog(conn, seed_path)
            elif version == 5:
                _migrate_monthly_catalog(conn, seed_path)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version > SCHEMA_VERSION:
            raise RuntimeError(
                f"La base requiere una versión más nueva ({version}) que esta aplicación."
            )

        count = conn.execute("SELECT COUNT(*) FROM reportes").fetchone()[0]
        if count == 0 and seed_path and resolve_path(seed_path).exists():
            seed_catalog(conn, resolve_path(seed_path))

        if legacy_no_sent_path:
            key = "migracion_no_enviados_v1"
            imported = conn.execute(
                "SELECT valor FROM app_meta WHERE clave = ?", (key,)
            ).fetchone()
            if imported is None:
                import_legacy_no_sent(conn, resolve_path(legacy_no_sent_path))
                conn.execute(
                    "INSERT OR REPLACE INTO app_meta(clave, valor) VALUES (?, ?)",
                    (key, now_iso()),
                )
        conn.commit()
    finally:
        conn.close()
    return path


def _ensure_rule_exception_columns(conn: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(reglas_reportes)")
    }
    if "excluir_ultimo_dia_mes" not in columns:
        conn.execute(
            "ALTER TABLE reglas_reportes "
            "ADD COLUMN excluir_ultimo_dia_mes INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_weekly_rules(conn: sqlite3.Connection) -> None:
    """Normaliza las reglas semanales antiguas sin tocar su historial."""
    timestamp = now_iso()
    rows = conn.execute(
        """
        SELECT rr.*, p.codigo, p.tipo_periodo
        FROM reglas_reportes rr
        JOIN reportes p ON p.id = rr.reporte_id
        WHERE rr.activo = 1 AND p.tipo_periodo = 'semanal'
        ORDER BY rr.id
        """
    ).fetchall()
    for row in rows:
        is_preamypes = row["codigo"] == "PREAMYPES_SEMANAL"
        target_day = 7 if is_preamypes else int(row["dia_semana_corte"] or 5)
        already_normalized = (
            row["regla_fecha_corte"] == "SEMANAL"
            and int(row["dia_semana_corte"] or 0) == target_day
            and _json_list(row["dias_envio"]) == []
            and row["hora_limite"] == WEEKLY_DEADLINE_TIME
            and row["dias_plazo"] is None
            and row["tipo_plazo"] is None
        )
        if already_normalized:
            continue

        # PREAMyPes cambió de corte viernes con envío permitido domingo a
        # corte real domingo. Una regla nueva mantiene intactas las
        # obligaciones históricas ligadas a la regla anterior.
        if is_preamypes and int(row["dia_semana_corte"] or 5) != target_day:
            conn.execute(
                "UPDATE reglas_reportes SET activo = 0, actualizado_en = ? WHERE id = ?",
                (timestamp, row["id"]),
            )
            _insert_rule(
                conn,
                row["reporte_id"],
                {
                    "regla_fecha_corte": "SEMANAL",
                    "dia_semana_corte": target_day,
                    "dias_envio": [],
                    "hora_limite": WEEKLY_DEADLINE_TIME,
                    "ocurrencias_requeridas": row["ocurrencias_requeridas"],
                    "dias_plazo": None,
                    "tipo_plazo": None,
                },
                timestamp,
            )
            continue

        conn.execute(
            """
            UPDATE reglas_reportes
            SET regla_fecha_corte = 'SEMANAL', dia_semana_corte = ?,
                dias_envio = '[]', hora_limite = ?, dias_plazo = NULL,
                tipo_plazo = NULL, actualizado_en = ?
            WHERE id = ?
            """,
            (target_day, WEEKLY_DEADLINE_TIME, timestamp, row["id"]),
        )


def _seed_default_holidays(conn: sqlite3.Connection) -> None:
    """Carga feriados nacionales sin sobrescribir ajustes existentes."""
    with conn:
        for holiday, description in default_holidays():
            conn.execute(
                "INSERT OR IGNORE INTO feriados(fecha, descripcion) VALUES (?, ?)",
                (holiday.isoformat(), description),
            )


def _migrate_monthly_catalog(
    conn: sqlite3.Connection, seed_path: Optional[str | Path]
) -> None:
    """Sincroniza reportes mensuales nuevos sin invalidar su historial."""
    timestamp = now_iso()
    for codigo in MONTHLY_RETIRED_CODES:
        row = conn.execute(
            "SELECT id FROM reportes WHERE codigo = ? AND tipo_periodo = 'mensual'",
            (codigo,),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE reportes
                   SET activo = 0, validacion_activa = 0, actualizado_en = ?
                 WHERE id = ?
                """,
                (timestamp, row["id"]),
            )
            conn.execute(
                "UPDATE reglas_reportes SET activo = 0, actualizado_en = ? WHERE reporte_id = ? AND activo = 1",
                (timestamp, row["id"]),
            )
            conn.execute(
                """
                UPDATE obligaciones
                   SET estado = 'DESCARTADO', actualizada_en = ?
                 WHERE reporte_id = ? AND estado NOT IN ('EXITOSO', 'DESCARTADO')
                """,
                (timestamp, row["id"]),
            )
            conn.execute(
                """
                UPDATE incumplimientos
                   SET estado = 'DESCARTADO', resuelto_en = ?
                 WHERE reporte_id = ? AND resuelto_en IS NULL
                """,
                (timestamp, row["id"]),
            )
    if not seed_path or not resolve_path(seed_path).exists():
        _seed_default_holidays(conn)
        return
    data = json.loads(resolve_path(seed_path).read_text(encoding="utf-8"))
    for item in data.get("reportes", data.get("reports", [])):
        if str(item.get("tipo_periodo", "")).lower() != "mensual":
            continue
        codigo = str(item["codigo"]).strip().upper()
        if codigo in MONTHLY_RETIRED_CODES:
            continue
        timestamp = now_iso()
        existing = conn.execute(
            "SELECT id FROM reportes WHERE codigo = ?", (codigo,)
        ).fetchone()
        if existing:
            report_id = int(existing["id"])
            conn.execute(
                """
                UPDATE reportes
                   SET nombre = ?, tipo_periodo = 'mensual', activo = 1,
                       validacion_activa = 1, descripcion = ?, actualizado_en = ?
                 WHERE id = ?
                """,
                (item["nombre"], item.get("descripcion", ""), timestamp, report_id),
            )
            conn.execute(
                "UPDATE reglas_reportes SET activo = 0, actualizado_en = ? WHERE reporte_id = ? AND activo = 1",
                (timestamp, report_id),
            )
            conn.execute("DELETE FROM alias_reportes WHERE reporte_id = ?", (report_id,))
        else:
            cursor = conn.execute(
                """
                INSERT INTO reportes
                  (codigo, nombre, tipo_periodo, activo, validacion_activa,
                   descripcion, creado_en, actualizado_en)
                VALUES (?, ?, 'mensual', 1, 1, ?, ?, ?)
                """,
                (codigo, item["nombre"], item.get("descripcion", ""), timestamp, timestamp),
            )
            report_id = int(cursor.lastrowid)

        aliases = [item["nombre"]] + item.get("aliases", [])
        for alias in dict.fromkeys(aliases):
            conn.execute(
                "INSERT OR IGNORE INTO alias_reportes(reporte_id, alias, alias_normalizado) VALUES (?, ?, ?)",
                (report_id, alias, normalize_name(alias)),
            )
        for rule in item.get("reglas", []):
            _insert_rule(conn, report_id, rule, timestamp)
    _seed_default_holidays(conn)


def seed_catalog(conn: sqlite3.Connection, seed_path: str | Path) -> None:
    data = json.loads(resolve_path(seed_path).read_text(encoding="utf-8"))
    reports = data.get("reportes", data.get("reports", []))
    for item in reports:
        if str(item.get("codigo", "")).strip().upper() in MONTHLY_RETIRED_CODES:
            continue
        timestamp = now_iso()
        cursor = conn.execute(
            """
            INSERT INTO reportes
              (codigo, nombre, tipo_periodo, activo, validacion_activa, descripcion,
               creado_en, actualizado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["codigo"],
                item["nombre"],
                item["tipo_periodo"],
                int(item.get("activo", True)),
                int(item.get("validacion_activa", True)),
                item.get("descripcion", ""),
                timestamp,
                timestamp,
            ),
        )
        report_id = cursor.lastrowid
        aliases = [item["nombre"]] + item.get("aliases", [])
        for alias in dict.fromkeys(aliases):
            conn.execute(
                "INSERT INTO alias_reportes(reporte_id, alias, alias_normalizado) VALUES (?, ?, ?)",
                (report_id, alias, normalize_name(alias)),
            )
        for rule in item.get("reglas", []):
            _insert_rule(conn, report_id, rule, timestamp)
    _seed_default_holidays(conn)


def _insert_rule(
    conn: sqlite3.Connection, report_id: int, rule: dict, timestamp: Optional[str] = None
) -> int:
    timestamp = timestamp or now_iso()
    rule = dict(rule)
    report_row = conn.execute(
        "SELECT tipo_periodo FROM reportes WHERE id = ?", (report_id,)
    ).fetchone()
    if report_row and str(report_row["tipo_periodo"]).lower() == "semanal":
        # En los semanales la ventana es siempre día posterior al corte hasta
        # el lunes siguiente a las 12:00. Los campos antiguos se conservan
        # en el esquema, pero ya no gobiernan este calendario.
        rule["regla_fecha_corte"] = "SEMANAL"
        rule["dia_semana_corte"] = int(rule.get("dia_semana_corte") or 5)
        rule["dias_envio"] = []
        rule["hora_limite"] = WEEKLY_DEADLINE_TIME
        rule["dias_plazo"] = None
        rule["tipo_plazo"] = None
    dias_envio = rule.get("dias_envio", [])
    if isinstance(dias_envio, str):
        dias_envio = [int(x.strip()) for x in dias_envio.split(",") if x.strip()]
    if any(int(day) < 1 or int(day) > 7 for day in dias_envio):
        raise ValueError("dias_envio debe contener valores entre 1 y 7")
    frequency = int(rule.get("frecuencia_meses", 1))
    if frequency < 1:
        raise ValueError("frecuencia_meses debe ser mayor que cero")
    if rule.get("dia_semana_corte") is not None and not 1 <= int(rule["dia_semana_corte"]) <= 7:
        raise ValueError("dia_semana_corte debe estar entre 1 y 7")
    if rule.get("dia_mes_corte") is not None and not 1 <= int(rule["dia_mes_corte"]) <= 31:
        raise ValueError("dia_mes_corte debe estar entre 1 y 31")
    if rule.get("mes_ancla") is not None and not 1 <= int(rule["mes_ancla"]) <= 12:
        raise ValueError("mes_ancla debe estar entre 1 y 12")
    parse_time(rule.get("hora_limite"))
    occurrences = int(rule.get("ocurrencias_requeridas", 1))
    if occurrences < 1:
        raise ValueError("ocurrencias_requeridas debe ser mayor que cero")
    cursor = conn.execute(
        """
        INSERT INTO reglas_reportes
          (reporte_id, regla_fecha_corte, dia_semana_corte, dia_mes_corte,
           mes_ancla, frecuencia_meses, dias_envio, ocurrencias_requeridas, hora_limite,
           dias_plazo, tipo_plazo, excluir_ultimo_dia_mes, activo,
           creado_en, actualizado_en)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            report_id,
            rule.get("regla_fecha_corte", "AYER"),
            rule.get("dia_semana_corte"),
            rule.get("dia_mes_corte"),
            rule.get("mes_ancla"),
            frequency,
            json.dumps(dias_envio),
            occurrences,
            rule.get("hora_limite"),
            rule.get("dias_plazo"),
            rule.get("tipo_plazo"),
            int(bool(rule.get("excluir_ultimo_dia_mes", False))),
            timestamp,
            timestamp,
        ),
    )
    return cursor.lastrowid


def get_catalog(conn: sqlite3.Connection, include_inactive: bool = True) -> list[dict]:
    where = "" if include_inactive else "WHERE activo = 1"
    rows = conn.execute(f"SELECT * FROM reportes {where} ORDER BY tipo_periodo, codigo").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        rules = conn.execute(
            "SELECT * FROM reglas_reportes WHERE reporte_id = ? AND activo = 1 ORDER BY id",
            (row["id"],),
        ).fetchall()
        aliases = conn.execute(
            "SELECT alias FROM alias_reportes WHERE reporte_id = ? ORDER BY id",
            (row["id"],),
        ).fetchall()
        item["reglas"] = []
        for rule in rules:
            rule_dict = dict(rule)
            rule_dict["dias_envio"] = _json_list(rule_dict["dias_envio"])
            item["reglas"].append(rule_dict)
        item["aliases"] = [alias["alias"] for alias in aliases]
        result.append(item)
    return result


def get_active_rules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT r.*, p.codigo, p.nombre, p.tipo_periodo, p.validacion_activa,
               p.activo AS reporte_activo
        FROM reglas_reportes r
        JOIN reportes p ON p.id = r.reporte_id
        WHERE r.activo = 1 AND p.activo = 1 AND p.validacion_activa = 1
        ORDER BY p.codigo, r.id
        """
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["dias_envio"] = _json_list(item["dias_envio"])
        result.append(item)
    return result


def lookup_report_id(conn: sqlite3.Connection, name: str) -> Optional[int]:
    row = conn.execute(
        "SELECT reporte_id FROM alias_reportes WHERE alias_normalizado = ?",
        (normalize_name(name),),
    ).fetchone()
    return row["reporte_id"] if row else None


def lookup_report_period(conn: sqlite3.Connection, name: str) -> Optional[str]:
    """Devuelve el tipo de período del reporte identificado por su nombre."""
    row = conn.execute(
        """
        SELECT p.tipo_periodo
        FROM reportes p
        JOIN alias_reportes a ON a.reporte_id = p.id
        WHERE a.alias_normalizado = ?
        """,
        (normalize_name(name),),
    ).fetchone()
    return row["tipo_periodo"] if row else None


def save_report(
    conn: sqlite3.Connection,
    report_id: Optional[int],
    codigo: str,
    nombre: str,
    tipo_periodo: str,
    activo: bool,
    validacion_activa: bool,
    descripcion: str,
    reglas: Iterable[dict],
    aliases: Iterable[str],
) -> int:
    codigo = codigo.strip().upper()
    nombre = nombre.strip()
    tipo_periodo = tipo_periodo.strip().lower()
    if not codigo or not nombre or not tipo_periodo:
        raise ValueError("Código, nombre y tipo de período son obligatorios")
    timestamp = now_iso()
    with conn:
        if report_id is None:
            cursor = conn.execute(
                """
                INSERT INTO reportes
                  (codigo, nombre, tipo_periodo, activo, validacion_activa,
                   descripcion, creado_en, actualizado_en)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    codigo,
                    nombre,
                    tipo_periodo,
                    int(activo),
                    int(validacion_activa),
                    descripcion.strip(),
                    timestamp,
                    timestamp,
                ),
            )
            report_id = cursor.lastrowid
        else:
            conn.execute(
                """
                UPDATE reportes
                SET codigo = ?, nombre = ?, tipo_periodo = ?, activo = ?,
                    validacion_activa = ?, descripcion = ?, actualizado_en = ?
                WHERE id = ?
                """,
                (
                    codigo,
                    nombre,
                    tipo_periodo,
                    int(activo),
                    int(validacion_activa),
                    descripcion.strip(),
                    timestamp,
                    report_id,
                ),
            )
            # Las obligaciones históricas conservan la regla antigua.
            conn.execute(
                "UPDATE reglas_reportes SET activo = 0, actualizado_en = ? WHERE reporte_id = ?",
                (timestamp, report_id),
            )
            conn.execute("DELETE FROM alias_reportes WHERE reporte_id = ?", (report_id,))

        all_aliases = [nombre] + [str(alias).strip() for alias in aliases if str(alias).strip()]
        for alias in dict.fromkeys(all_aliases):
            conn.execute(
                "INSERT INTO alias_reportes(reporte_id, alias, alias_normalizado) VALUES (?, ?, ?)",
                (report_id, alias, normalize_name(alias)),
            )
        for rule in reglas:
            _insert_rule(conn, report_id, dict(rule), timestamp)
    return int(report_id)


def set_report_flags(conn: sqlite3.Connection, report_id: int, activo: bool, validacion_activa: bool) -> None:
    conn.execute(
        "UPDATE reportes SET activo = ?, validacion_activa = ?, actualizado_en = ? WHERE id = ?",
        (int(activo), int(validacion_activa), now_iso(), report_id),
    )
    conn.commit()


def save_credentials(conn: sqlite3.Connection, usuario: str, password: str) -> None:
    usuario = usuario.strip()
    if not usuario or not password:
        raise ValueError("Usuario y contraseña son obligatorios")
    conn.execute(
        """
        INSERT INTO credenciales(id, usuario, password_protegida, actualizado_en)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          usuario = excluded.usuario,
          password_protegida = excluded.password_protegida,
          actualizado_en = excluded.actualizado_en
        """,
        (usuario, _protect_secret(password), now_iso()),
    )
    conn.commit()


def get_credentials(conn: sqlite3.Connection) -> tuple[str, str]:
    row = conn.execute(
        "SELECT usuario, password_protegida FROM credenciales WHERE id = 1"
    ).fetchone()
    if not row:
        return "", ""
    return row["usuario"], _unprotect_secret(row["password_protegida"])


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _previous_month_end(value: date) -> date:
    first = date(value.year, value.month, 1)
    return first - timedelta(days=1)


def _period_end(value: date, frequency: int, anchor_month: int = 1) -> date:
    frequency = max(1, int(frequency or 1))
    candidate = _month_end(value.year, value.month)
    if candidate > value:
        candidate = _previous_month_end(value)
    for _ in range(240):
        if ((candidate.month - anchor_month) % frequency) == (frequency - 1):
            return candidate
        candidate = _previous_month_end(candidate)
    return candidate


def _add_business_days(
    start: date, days: int, holidays: Optional[set[date]] = None
) -> date:
    result = start
    remaining = max(0, int(days))
    holidays = holidays or set()
    while remaining:
        result += timedelta(days=1)
        if result.weekday() < 5 and result not in holidays:
            remaining -= 1
    return result


def _deadline_date(
    cutoff: date,
    days: Optional[int],
    tipo_plazo: Optional[str],
    holidays: Optional[set[date]] = None,
) -> Optional[date]:
    if days is None:
        return None
    if str(tipo_plazo or "calendario").lower() in {"habil", "habiles", "hábil", "hábiles"}:
        return _add_business_days(cutoff, int(days), holidays)
    return cutoff + timedelta(days=int(days))


def calculate_obligation(
    rule: dict, today: date, holidays: Optional[set[date]] = None
) -> Optional[dict]:
    """Calcula una obligación concreta para una regla y la fecha actual."""
    kind = str(rule.get("regla_fecha_corte", "AYER")).upper()
    cutoff = None
    window_start = None
    deadline_day = None
    deadline_hour = rule.get("hora_limite")

    if is_weekly_rule(rule):
        target = int(rule.get("dia_semana_corte") or 5)
        if not 1 <= target <= 7:
            raise ValueError("dia_semana_corte debe estar entre 1 y 7")
        cutoff = today - timedelta(days=(today.isoweekday() - target) % 7)
        window_start = cutoff + timedelta(days=1)
        # ASFI muestra el límite el domingo; en el monitor corresponde al
        # lunes siguiente a las 12:00 por la fecha de corte "ayer".
        deadline_day = _next_monday_after(cutoff)
        deadline_hour = WEEKLY_DEADLINE_TIME
        period_key = cutoff.isoformat()
    elif kind == "AYER":
        cutoff = today - timedelta(days=1)
        expected_weekday = rule.get("dia_semana_corte")
        if expected_weekday and cutoff.isoweekday() != int(expected_weekday):
            return None
        # La ventana arranca en la fecha de corte: SCIP acepta envíos el
        # mismo día del corte por la tarde (p. ej. D008 a las 15:56).
        window_start = cutoff
        deadline_day = today
        period_key = cutoff.isoformat()
    elif kind in {"VIERNES", "SEMANA"}:
        target = int(rule.get("dia_semana_corte") or 5)
        cutoff = today - timedelta(days=(today.isoweekday() - target) % 7)
        allowed = [int(x) for x in _json_list(rule.get("dias_envio"))]
        if not allowed:
            allowed = [target, 6, 7]
        offsets = [(day - target) % 7 for day in allowed]
        window_start = cutoff + timedelta(days=min(offsets))
        deadline_day = cutoff + timedelta(days=max(offsets))
        period_key = cutoff.isoformat()
    elif kind in {"FIN_MES", "FIN_PERIODO"}:
        frequency = int(rule.get("frecuencia_meses") or 1)
        anchor_month = int(rule.get("mes_ancla") or 1)
        cutoff = _period_end(today, frequency, anchor_month)
        window_start = cutoff
        deadline_day = _deadline_date(
            cutoff, rule.get("dias_plazo"), rule.get("tipo_plazo"), holidays
        )
        period_key = cutoff.strftime("%Y-%m") if frequency == 1 else cutoff.isoformat()
    else:
        return None

    if (
        rule.get("excluir_ultimo_dia_mes")
        and deadline_day is not None
        and deadline_day == _month_end(deadline_day.year, deadline_day.month)
    ):
        return None

    deadline = _combine_date_time(deadline_day, deadline_hour) if deadline_day else None
    return {
        "reporte_id": rule["reporte_id"],
        "regla_id": rule["id"],
        "codigo": rule["codigo"],
        "nombre": rule["nombre"],
        "tipo_periodo": rule["tipo_periodo"],
        "periodo_clave": period_key,
        "fecha_corte": cutoff.isoformat(),
        "fecha_inicio_envio": window_start.isoformat() if window_start else None,
        "fecha_hora_limite": deadline.isoformat(timespec="seconds") if deadline else None,
        "ocurrencias_requeridas": int(rule.get("ocurrencias_requeridas") or 1),
    }


def ensure_obligations(conn: sqlite3.Connection, current: Optional[datetime] = None) -> list[dict]:
    current = current or local_now()
    today = current.date()
    holidays = get_holidays(conn)
    created = []
    with conn:
        for rule in get_active_rules(conn):
            calculated = calculate_obligation(rule, today, holidays)
            if not calculated:
                continue
            timestamp = now_iso(current)
            for occurrence in range(1, calculated["ocurrencias_requeridas"] + 1):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO obligaciones
                      (reporte_id, regla_id, periodo_clave, fecha_corte,
                       fecha_inicio_envio, fecha_hora_limite, ocurrencia,
                       estado, creada_en, actualizada_en)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        calculated["reporte_id"],
                        calculated["regla_id"],
                        calculated["periodo_clave"],
                        calculated["fecha_corte"],
                        calculated["fecha_inicio_envio"],
                        calculated["fecha_hora_limite"],
                        occurrence,
                        "ABIERTO" if calculated["fecha_hora_limite"] else "CONFIGURAR",
                        timestamp,
                        timestamp,
                    ),
                )
            conn.execute(
                """
                UPDATE obligaciones
                   SET fecha_inicio_envio = ?, fecha_hora_limite = ?, actualizada_en = ?
                 WHERE regla_id = ? AND periodo_clave = ?
                   AND estado IN ('ABIERTO', 'PENDIENTE', 'FALTANTE')
                """,
                (
                    calculated["fecha_inicio_envio"],
                    calculated["fecha_hora_limite"],
                    timestamp,
                    calculated["regla_id"],
                    calculated["periodo_clave"],
                ),
            )
            rows = conn.execute(
                """
                SELECT o.*, p.codigo, p.nombre, p.tipo_periodo
                FROM obligaciones o JOIN reportes p ON p.id = o.reporte_id
                WHERE o.regla_id = ? AND o.periodo_clave = ?
                ORDER BY o.ocurrencia
                """,
                (calculated["regla_id"], calculated["periodo_clave"]),
            ).fetchall()
            created.extend(dict(row) for row in rows)
    return created


def get_required_occurrence_counts(
    conn: sqlite3.Connection, reportes: Iterable[dict]
) -> dict[tuple[str, str], int]:
    """Devuelve cuántas ocurrencias exige cada reporte y fecha de corte.

    La cantidad se obtiene de las obligaciones activas ya calculadas, por lo
    que respeta la regla de calendario vigente para cada período.
    """
    counts: dict[tuple[str, str], int] = {}
    for reporte in reportes:
        nombre = normalize_name(str(reporte.get("grupo") or ""))
        cutoff = parse_date(reporte.get("fecha_corte"))
        if not nombre or cutoff is None:
            continue

        report_id = lookup_report_id(conn, reporte.get("grupo", ""))
        if report_id is None:
            continue

        cutoff_iso = cutoff.isoformat()
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM obligaciones o
            JOIN reglas_reportes rr ON rr.id = o.regla_id AND rr.activo = 1
            WHERE o.reporte_id = ? AND o.fecha_corte = ?
            """,
            (report_id, cutoff_iso),
        ).fetchone()[0]
        if count:
            key = (nombre, cutoff_iso)
            counts[key] = max(counts.get(key, 0), int(count))

    return counts


def get_query_date_ranges(
    conn: sqlite3.Connection,
    current: Optional[datetime] = None,
    lookback_days: int = 62,
    configured_start: Optional[str | date] = None,
    configured_end: Optional[str | date] = None,
) -> list[tuple[date, date]]:
    """Devuelve rangos separados para consultar las fechas necesarias en ASFI.

    El rango diario/semanal termina en ayer porque SCIP no expone el día en
    curso. Los cortes mensuales se consultan en rangos unitarios separados para
    no traer todas las fechas intermedias del mes.
    """
    current = current or local_now()
    if configured_start is not None or configured_end is not None:
        start = parse_date(configured_start)
        end = parse_date(configured_end)
        if start is None or end is None:
            raise ValueError("El rango manual requiere fecha inicial y final válidas")
        if start > end:
            raise ValueError("La fecha inicial no puede ser posterior a la fecha final")
        return [(start, end)]

    yesterday = current.date() - timedelta(days=1)
    obligations = ensure_obligations(conn, current)
    active_weekly_cutoffs = []
    active_monthly_cutoffs = []
    for row in obligations:
        if row["tipo_periodo"] not in {"semanal", "mensual"}:
            continue
        cutoff = parse_date(row["fecha_corte"])
        start = parse_date(row["fecha_inicio_envio"])
        deadline = parse_datetime(row["fecha_hora_limite"])
        if (
            cutoff is not None
            and start is not None
            and deadline is not None
            and current.date() >= start
            and _as_aware(current) <= _as_aware(deadline)
        ):
            target = (
                active_monthly_cutoffs
                if row["tipo_periodo"] == "mensual"
                else active_weekly_cutoffs
            )
            if cutoff not in target:
                target.append(cutoff)

    weekly_start = min([yesterday, *active_weekly_cutoffs])
    ranges = [(weekly_start, yesterday)]
    ranges.extend((cutoff, cutoff) for cutoff in active_monthly_cutoffs)
    ranges.sort()

    merged: list[tuple[date, date]] = []
    for start, end in ranges:
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def get_query_date_range(
    conn: sqlite3.Connection,
    current: Optional[datetime] = None,
    lookback_days: int = 62,
    configured_start: Optional[str | date] = None,
    configured_end: Optional[str | date] = None,
) -> tuple[date, date]:
    """Devuelve el rango envolvente por compatibilidad con llamadores antiguos."""
    ranges = get_query_date_ranges(
        conn,
        current,
        lookback_days,
        configured_start,
        configured_end,
    )
    return min(start for start, _end in ranges), max(end for _start, end in ranges)


def start_scrape_run(conn: sqlite3.Connection, start: date, end: date) -> int:
    cursor = conn.execute(
        """
        INSERT INTO ejecuciones_scraping
          (iniciado_en, fecha_inicio_consulta, fecha_fin_consulta, resultado)
        VALUES (?, ?, ?, 'EN_PROCESO')
        """,
        (now_iso(), start.isoformat(), end.isoformat()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_scrape_run(
    conn: sqlite3.Connection,
    run_id: int,
    result: str,
    count: int,
    error: Optional[str] = None,
) -> None:
    conn.execute(
        """
        UPDATE ejecuciones_scraping
        SET finalizado_en = ?, cantidad_resultados = ?, resultado = ?, detalle_error = ?
        WHERE id = ?
        """,
        (now_iso(), count, result, error, run_id),
    )
    conn.commit()


def extract_occurrence(envio: str) -> Optional[int]:
    match = re.search(r"env[ií]o\s*(\d+)", envio or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def _extract_occurrence(envio: str) -> Optional[int]:
    """Compatibilidad interna para llamadores existentes."""
    return extract_occurrence(envio)


def store_observations(conn: sqlite3.Connection, run_id: int, reportes: list[dict]) -> None:
    groups: dict[tuple[Optional[int], Optional[str]], list[tuple[int, dict]]] = {}
    with conn:
        for reporte in reportes:
            nombre = reporte.get("grupo", "")
            parsed_cutoff = parse_date(reporte.get("fecha_corte"))
            cutoff_iso = parsed_cutoff.isoformat() if parsed_cutoff else None
            report_id = lookup_report_id(conn, nombre)
            cursor = conn.execute(
                """
                INSERT INTO observaciones_reportes
                  (ejecucion_id, reporte_id, nombre_raw, nombre_normalizado,
                   fecha_corte_raw, fecha_corte, fecha_llegada, tipo_entidad,
                   sigla, email, resultado_raw, validacion, envio, estado,
                   detalle, observado_en)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    report_id,
                    nombre,
                    normalize_name(nombre),
                    reporte.get("fecha_corte"),
                    cutoff_iso,
                    reporte.get("fecha_llegada"),
                    reporte.get("tipo_entidad"),
                    reporte.get("sigla"),
                    reporte.get("email"),
                    reporte.get("resultado_raw"),
                    reporte.get("validacion"),
                    reporte.get("envio"),
                    reporte.get("estado", "DESCONOCIDO"),
                    reporte.get("detalle"),
                    reporte.get("timestamp_revision") or now_iso(),
                ),
            )
            key = (report_id, cutoff_iso)
            groups.setdefault(key, []).append((int(cursor.lastrowid), reporte))

        for (report_id, cutoff_iso), observations in groups.items():
            if report_id is None or cutoff_iso is None:
                continue
            obligations = conn.execute(
                """
                SELECT o.* FROM obligaciones o
                JOIN reglas_reportes rr ON rr.id = o.regla_id AND rr.activo = 1
                WHERE o.reporte_id = ? AND o.fecha_corte = ?
                ORDER BY ocurrencia
                """,
                (report_id, cutoff_iso),
            ).fetchall()
            rows_with_ids = [
                {**reporte, "_observacion_id": observation_id}
                for observation_id, reporte in observations
            ]
            occurrence_map = _rows_by_occurrence(
                rows_with_ids,
                [dict(row) for row in obligations],
            )
            by_occurrence = {row["ocurrencia"]: row for row in obligations}
            for occurrence, occurrence_rows in occurrence_map.items():
                obligation = by_occurrence.get(occurrence)
                if obligation is None:
                    continue
                for reporte in occurrence_rows:
                    conn.execute(
                        "INSERT OR IGNORE INTO obligacion_observacion(obligacion_id, observacion_id) VALUES (?, ?)",
                        (obligation["id"], reporte["_observacion_id"]),
                    )


def _rows_by_occurrence(rows: list[dict], obligations: list[dict]) -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = {}
    if len(obligations) == 1:
        return {obligations[0]["ocurrencia"]: list(rows)} if rows else {}
    valid_occurrences = {obligation["ocurrencia"] for obligation in obligations}
    by_number: dict[int, list[dict]] = {}
    unnumbered: list[dict] = []
    for row in rows:
        number = extract_occurrence(row.get("envio", ""))
        if number in valid_occurrences:
            by_number.setdefault(number, []).append(row)
        elif number is None:
            unnumbered.append(row)

    for obligation in obligations:
        occurrence = obligation["ocurrencia"]
        if by_number.get(occurrence):
            result[occurrence] = by_number[occurrence]
        elif unnumbered:
            result[occurrence] = [unnumbered.pop(0)]
    return result


def _observation_in_window(row: dict, obligation: sqlite3.Row, current: datetime) -> bool:
    start = parse_date(obligation["fecha_inicio_envio"])
    deadline = parse_datetime(obligation["fecha_hora_limite"])
    arrival = parse_datetime(row.get("fecha_llegada"))
    if arrival is None:
        # La fecha no está disponible en algunas respuestas de SCIP; no se
        # descarta el resultado para evitar falsos faltantes.
        return True
    if start is not None and arrival.date() < start:
        return False
    if deadline is not None and _as_aware(arrival) > _as_aware(deadline):
        return False
    return True


def _observation_is_late(row: dict, obligation: sqlite3.Row) -> bool:
    """Indica si un envío llegó después de la fecha y hora límite."""
    arrival = parse_datetime(row.get("fecha_llegada"))
    deadline = parse_datetime(obligation["fecha_hora_limite"])
    return (
        arrival is not None
        and deadline is not None
        and _as_aware(arrival) > _as_aware(deadline)
    )


def _linked_observations(
    conn: sqlite3.Connection, obligation_ids: Iterable[int]
) -> dict[int, list[dict]]:
    ids = [int(item) for item in obligation_ids]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT oo.obligacion_id, obs.*
        FROM obligacion_observacion oo
        JOIN observaciones_reportes obs ON obs.id = oo.observacion_id
        WHERE oo.obligacion_id IN ({placeholders})
        ORDER BY obs.observado_en, obs.id
        """,
        ids,
    ).fetchall()
    result: dict[int, list[dict]] = {}
    for row in rows:
        item = dict(row)
        obligation_id = int(item.pop("obligacion_id"))
        result.setdefault(obligation_id, []).append(item)
    return result


def evaluate_obligations(
    conn: sqlite3.Connection, reportes: list[dict], current: Optional[datetime] = None
) -> dict[str, list[str]]:
    current = current or local_now()
    # Las obligaciones vigentes y las vencidas sin resolver deben seguir
    # evaluándose aunque la respuesta actual de SCIP no repita sus filas.
    current_obligations = ensure_obligations(conn, current)
    grouped: dict[tuple[Optional[int], Optional[str]], list[dict]] = {}
    for reporte in reportes:
        report_id = lookup_report_id(conn, reporte.get("grupo", ""))
        cutoff = parse_date(reporte.get("fecha_corte"))
        if report_id is not None and cutoff is not None:
            grouped.setdefault((report_id, cutoff.isoformat()), []).append(reporte)

    candidate_ids = {int(row["id"]) for row in current_obligations}
    for report_id, cutoff in grouped:
        rows = conn.execute(
            """
            SELECT o.id
            FROM obligaciones o
            JOIN reportes p ON p.id = o.reporte_id
            JOIN reglas_reportes rr ON rr.id = o.regla_id AND rr.activo = 1
            WHERE p.activo = 1 AND p.validacion_activa = 1
              AND o.estado <> 'DESCARTADO'
              AND o.reporte_id = ? AND o.fecha_corte = ?
            """,
            (report_id, cutoff),
        ).fetchall()
        candidate_ids.update(int(row["id"]) for row in rows)

    # Si el monitor estuvo apagado al cambiar de semana, llevar igualmente a
    # vencido cualquier obligación cuyo límite ya haya pasado.
    overdue_candidates = conn.execute(
        """
        SELECT o.id, o.fecha_hora_limite
        FROM obligaciones o
        JOIN reportes p ON p.id = o.reporte_id
        JOIN reglas_reportes rr ON rr.id = o.regla_id AND rr.activo = 1
        WHERE p.activo = 1 AND p.validacion_activa = 1
              AND p.tipo_periodo IN ('semanal', 'mensual')
          AND o.estado NOT IN ('EXITOSO', 'DESCARTADO')
          AND o.fecha_hora_limite IS NOT NULL
        """
    ).fetchall()
    for row in overdue_candidates:
        deadline = parse_datetime(row["fecha_hora_limite"])
        if deadline is not None and _as_aware(current) > _as_aware(deadline):
            candidate_ids.add(int(row["id"]))

    if not candidate_ids:
        return {
            "diario": [],
            "semanal": [],
            "mensual": [],
            "otros": [],
            "tardios": {"diario": [], "semanal": [], "mensual": [], "otros": []},
        }

    placeholders = ",".join("?" for _ in candidate_ids)
    candidates = conn.execute(
        f"""
        SELECT o.*, p.codigo, p.nombre, p.tipo_periodo
        FROM obligaciones o
        JOIN reportes p ON p.id = o.reporte_id
        JOIN reglas_reportes rr ON rr.id = o.regla_id AND rr.activo = 1
        WHERE p.activo = 1 AND p.validacion_activa = 1
          AND o.estado <> 'DESCARTADO'
          AND o.id IN ({placeholders})
        ORDER BY o.fecha_corte, p.codigo, o.ocurrencia
        """,
        list(candidate_ids),
    ).fetchall()

    candidate_groups: dict[tuple[int, str], list[sqlite3.Row]] = {}
    for row in candidates:
        candidate_groups.setdefault((row["reporte_id"], row["fecha_corte"]), []).append(row)

    current_observations: dict[int, list[dict]] = {}
    current_late_observations: dict[int, list[dict]] = {}
    for key, obligation_rows in candidate_groups.items():
        rows_for_key = grouped.get(key, [])
        filtered_rows = [
            item
            for item in rows_for_key
            if _observation_in_window(item, obligation_rows[0], current)
        ]
        occurrence_map = _rows_by_occurrence(
            filtered_rows, [dict(row) for row in obligation_rows]
        )
        for row in obligation_rows:
            current_observations[int(row["id"])] = occurrence_map.get(row["ocurrencia"], [])
            if row["tipo_periodo"] in ("semanal", "mensual"):
                late_rows = [
                    item
                    for item in rows_for_key
                    if _observation_is_late(item, row)
                ]
                late_map = _rows_by_occurrence(
                    late_rows, [dict(item) for item in obligation_rows]
                )
                current_late_observations[int(row["id"])] = late_map.get(
                    row["ocurrencia"], []
                )

    history = _linked_observations(conn, [row["id"] for row in candidates])
    missing = {"diario": [], "semanal": [], "mensual": [], "otros": []}
    late = {"diario": [], "semanal": [], "mensual": [], "otros": []}
    timestamp = now_iso(current)
    with conn:
        for row in candidates:
            obligation_id = int(row["id"])
            occurrence_rows = []
            if row["tipo_periodo"] in ("semanal", "mensual"):
                occurrence_rows = [
                    item
                    for item in history.get(obligation_id, [])
                    if _observation_in_window(item, row, current)
                ]
            occurrence_rows.extend(current_observations.get(obligation_id, []))
            late_rows = []
            if row["tipo_periodo"] in ("semanal", "mensual"):
                late_rows = [
                    item
                    for item in history.get(obligation_id, [])
                    if _observation_is_late(item, row)
                ]
                late_rows.extend(current_late_observations.get(obligation_id, []))
            statuses = {item.get("estado") for item in occurrence_rows}
            late_statuses = {item.get("estado") for item in late_rows}
            deadline = parse_datetime(row["fecha_hora_limite"])
            expired = deadline is not None and _as_aware(current) > _as_aware(deadline)
            weekly = row["tipo_periodo"] == "semanal"

            if "EXITOSO" in statuses:
                status = "EXITOSO"
            elif row["tipo_periodo"] in ("semanal", "mensual") and "EXITOSO" in late_statuses:
                status = "EXITOSO_TARDIO"
            elif weekly and expired:
                status = "VENCIDO_SIN_ENVIAR"
            elif "ERROR" in statuses:
                status = "ERROR"
            elif row["tipo_periodo"] in ("semanal", "mensual") and "ERROR" in late_statuses:
                status = "ERROR"
            elif "PENDIENTE" in statuses:
                status = "PENDIENTE"
            elif not row["fecha_hora_limite"]:
                status = "CONFIGURAR"
            else:
                status = "FALTANTE" if expired else "ABIERTO"

            conn.execute(
                "UPDATE obligaciones SET estado = ?, ultima_revision = ?, actualizada_en = ? WHERE id = ?",
                (status, timestamp, timestamp, obligation_id),
            )

            if status in ("FALTANTE", "VENCIDO_SIN_ENVIAR"):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO incumplimientos
                      (obligacion_id, reporte_id, nombre_snapshot, tipo_periodo,
                       fecha_corte, ocurrencia, detectado_en, estado)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        obligation_id,
                        row["reporte_id"],
                        row["nombre"],
                        row["tipo_periodo"],
                        row["fecha_corte"],
                        row["ocurrencia"],
                        timestamp,
                        status,
                    ),
                )
                conn.execute(
                    """
                    UPDATE incumplimientos
                    SET estado = ?
                    WHERE obligacion_id = ? AND resuelto_en IS NULL
                    """,
                    (status, obligation_id),
                )
                tipo = row["tipo_periodo"] if row["tipo_periodo"] in missing else "otros"
                missing[tipo].append(row["nombre"])
            elif status == "EXITOSO":
                conn.execute(
                    """
                    UPDATE incumplimientos
                    SET resuelto_en = ?, estado = 'RESUELTO'
                    WHERE obligacion_id = ? AND resuelto_en IS NULL
                    """,
                    (timestamp, obligation_id),
                )
            elif status == "EXITOSO_TARDIO":
                conn.execute(
                    """
                    INSERT OR IGNORE INTO incumplimientos
                      (obligacion_id, reporte_id, nombre_snapshot, tipo_periodo,
                       fecha_corte, ocurrencia, detectado_en, estado)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        obligation_id,
                        row["reporte_id"],
                        row["nombre"],
                        row["tipo_periodo"],
                        row["fecha_corte"],
                        row["ocurrencia"],
                        timestamp,
                        status,
                    ),
                )
                conn.execute(
                    """
                    UPDATE incumplimientos
                    SET estado = ?, resuelto_en = NULL
                    WHERE obligacion_id = ? AND resuelto_en IS NULL
                    """,
                    (status, obligation_id),
                )
                tipo = row["tipo_periodo"] if row["tipo_periodo"] in late else "otros"
                late[tipo].append(row["nombre"])

    return {**missing, "tardios": late}


def import_legacy_no_sent(conn: sqlite3.Connection, path: Path) -> None:
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, list):
        return
    for item in data:
        nombre = str(item.get("reporte", "")).strip()
        cutoff = parse_date(item.get("fecha_esperada"))
        if not nombre or cutoff is None:
            continue
        report_id = lookup_report_id(conn, nombre)
        conn.execute(
            """
            INSERT INTO incumplimientos
              (reporte_id, nombre_snapshot, tipo_periodo, fecha_corte,
               ocurrencia, detectado_en, resuelto_en, estado)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                report_id,
                nombre,
                item.get("tipo", "diario"),
                cutoff.isoformat(),
                item.get("registrado") or now_iso(),
                item.get("enviado_despues_detectado"),
                "RESUELTO" if item.get("enviado_despues") else "FALTANTE",
            ),
        )


def list_failures(conn: sqlite3.Connection, unresolved_only: bool = False) -> list[dict]:
    condition = (
        "WHERE i.resuelto_en IS NULL AND i.estado IN ('FALTANTE', 'VENCIDO_SIN_ENVIAR', 'EXITOSO_TARDIO')"
        if unresolved_only
        else ""
    )
    rows = conn.execute(
        f"""
        SELECT i.*, p.codigo
        FROM incumplimientos i LEFT JOIN reportes p ON p.id = i.reporte_id
        {condition}
        ORDER BY i.fecha_corte, i.nombre_snapshot, i.ocurrencia
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_unresolved_failures(conn: sqlite3.Connection) -> list[dict]:
    return list_failures(conn, unresolved_only=True)


def list_overdue_obligations(
    conn: sqlite3.Connection, current: Optional[datetime] = None
) -> list[dict]:
    """Obligaciones con hora límite superada y sin éxito en plazo.

    Devuelve además la fecha y la hora límite de envío por separado para
    mostrarlas en la interfaz ("debía enviarse" y "hora máxima"). Conserva
    ``EXITOSO_TARDIO`` para diferenciar envíos fuera de plazo.
    """
    current = current or local_now()
    rows = conn.execute(
        """
        SELECT o.id, o.periodo_clave, o.fecha_corte, o.fecha_hora_limite,
               o.ocurrencia, o.estado, o.actualizada_en,
               (
                   SELECT obs.fecha_llegada
                   FROM obligacion_observacion oo
                   JOIN observaciones_reportes obs ON obs.id = oo.observacion_id
                   WHERE oo.obligacion_id = o.id
                     AND obs.fecha_llegada IS NOT NULL
                     AND TRIM(obs.fecha_llegada) <> ''
                   ORDER BY obs.observado_en DESC, obs.id DESC
                   LIMIT 1
               ) AS fecha_envio,
               p.codigo, p.nombre, p.tipo_periodo
        FROM obligaciones o
        JOIN reportes p ON p.id = o.reporte_id
        JOIN reglas_reportes rr ON rr.id = o.regla_id AND rr.activo = 1
        WHERE p.activo = 1 AND p.validacion_activa = 1
          AND o.estado NOT IN ('EXITOSO', 'DESCARTADO')
          AND o.fecha_hora_limite IS NOT NULL
        ORDER BY o.fecha_hora_limite, p.codigo, o.ocurrencia
        """,
    ).fetchall()
    overdue: list[dict] = []
    for row in rows:
        deadline = parse_datetime(row["fecha_hora_limite"])
        if deadline is None:
            continue
        if _as_aware(current) > _as_aware(deadline):
            item = dict(row)
            item["fecha_limite"] = deadline.date().isoformat()
            item["hora_limite"] = deadline.strftime("%H:%M")
            if item["estado"] == "EXITOSO_TARDIO":
                pass
            elif item["tipo_periodo"] == "semanal":
                item["estado"] = "VENCIDO_SIN_ENVIAR"
            elif item["tipo_periodo"] == "mensual":
                item["estado"] = "FALTANTE"
            overdue.append(item)
    return overdue


def dismiss_obligation(
    conn: sqlite3.Connection, obligation_id: int, current: Optional[datetime] = None
) -> bool:
    """Descarta una obligación no exitosa: deja de listarse y de alertar.

    El estado DESCARTADO se conserva como registro y resuelve cualquier
    incumplimiento asociado. Devuelve False si la obligación ya era EXITOSA
    o estaba descartada.
    """
    current = current or local_now()
    timestamp = now_iso(current)
    row = conn.execute(
        "SELECT estado FROM obligaciones WHERE id = ?", (obligation_id,)
    ).fetchone()
    if not row or row["estado"] in ("EXITOSO", "DESCARTADO"):
        return False
    with conn:
        conn.execute(
            "UPDATE obligaciones SET estado = 'DESCARTADO', actualizada_en = ? WHERE id = ?",
            (timestamp, obligation_id),
        )
        conn.execute(
            "UPDATE incumplimientos SET resuelto_en = ? WHERE obligacion_id = ? AND resuelto_en IS NULL",
            (timestamp, obligation_id),
        )
    return True


def dismiss_overdue_obligations(
    conn: sqlite3.Connection, current: Optional[datetime] = None
) -> int:
    """Descarta todas las obligaciones vencidas sin envío. Devuelve cuántas."""
    rows = list_overdue_obligations(conn, current)
    for row in rows:
        dismiss_obligation(conn, row["id"], current)
    return len(rows)


def list_current_obligations(conn: sqlite3.Connection) -> list[dict]:
    """Obligaciones del período vigente por regla (estado del día)."""
    rows = conn.execute(
        """
        SELECT o.id, o.periodo_clave, o.fecha_corte, o.fecha_inicio_envio,
               o.fecha_hora_limite, o.ocurrencia, o.estado, o.ultima_revision,
               p.codigo, p.nombre, p.tipo_periodo
        FROM obligaciones o
        JOIN reportes p ON p.id = o.reporte_id
        JOIN reglas_reportes rr ON rr.id = o.regla_id AND rr.activo = 1
        WHERE p.activo = 1 AND p.validacion_activa = 1
          AND o.estado <> 'DESCARTADO'
          AND o.periodo_clave = (
              SELECT MAX(o2.periodo_clave)
              FROM obligaciones o2
              JOIN reglas_reportes rr2 ON rr2.id = o2.regla_id AND rr2.activo = 1
              WHERE o2.regla_id = o.regla_id
          )
        ORDER BY CASE p.tipo_periodo
                     WHEN 'diario' THEN 0
                     WHEN 'semanal' THEN 1
                     ELSE 2
                 END, p.codigo, o.ocurrencia
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_today_obligations(
    conn: sqlite3.Connection, current: Optional[datetime] = None
) -> list[dict]:
    """Obligaciones calculadas para la fecha actual, sin arrastrar días anteriores."""
    calculated = ensure_obligations(conn, current or local_now())
    ids = [row["id"] for row in calculated]
    if not ids:
        return []

    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT o.id, o.periodo_clave, o.fecha_corte, o.fecha_inicio_envio,
               o.fecha_hora_limite, o.ocurrencia, o.estado, o.ultima_revision,
               (
                   SELECT obs.fecha_llegada
                   FROM obligacion_observacion oo
                   JOIN observaciones_reportes obs ON obs.id = oo.observacion_id
                   WHERE oo.obligacion_id = o.id
                     AND obs.fecha_llegada IS NOT NULL
                     AND TRIM(obs.fecha_llegada) <> ''
                   ORDER BY obs.observado_en DESC, obs.id DESC
                   LIMIT 1
               ) AS fecha_envio,
               p.codigo, p.nombre, p.tipo_periodo
        FROM obligaciones o
        JOIN reportes p ON p.id = o.reporte_id
        JOIN reglas_reportes rr ON rr.id = o.regla_id AND rr.activo = 1
        WHERE p.activo = 1 AND p.validacion_activa = 1
          AND o.estado <> 'DESCARTADO'
          AND o.id IN ({placeholders})
        ORDER BY CASE p.tipo_periodo
                     WHEN 'diario' THEN 0
                     WHEN 'semanal' THEN 1
                     ELSE 2
                 END, p.codigo, o.ocurrencia
        """,
        ids,
    ).fetchall()
    return [dict(row) for row in rows]


def credentials_updated_at(conn: sqlite3.Connection) -> Optional[str]:
    """Fecha de última actualización de las credenciales guardadas (o None)."""
    row = conn.execute(
        "SELECT actualizado_en FROM credenciales WHERE id = 1"
    ).fetchone()
    return row["actualizado_en"] if row else None
