"""Persistencia SQLite para el catálogo y el historial del monitor ASFI."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import sqlite3
import unicodedata
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 sin zoneinfo
    ZoneInfo = None


SCHEMA_VERSION = 2
DEFAULT_TIMEZONE = "America/La_Paz"
try:
    _LOCAL_TZ = ZoneInfo(DEFAULT_TIMEZONE) if ZoneInfo else timezone(
        timedelta(hours=-4), DEFAULT_TIMEZONE
    )
except Exception:  # Windows puede no tener tzdata instalado
    _LOCAL_TZ = timezone(timedelta(hours=-4), DEFAULT_TIMEZONE)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_meta (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reportes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE,
    nombre TEXT NOT NULL,
    tipo_periodo TEXT NOT NULL,
    activo INTEGER NOT NULL DEFAULT 1,
    validacion_activa INTEGER NOT NULL DEFAULT 1,
    descripcion TEXT NOT NULL DEFAULT '',
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reglas_reportes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporte_id INTEGER NOT NULL REFERENCES reportes(id),
    regla_fecha_corte TEXT NOT NULL,
    dia_semana_corte INTEGER,
    dia_mes_corte INTEGER,
    mes_ancla INTEGER,
    frecuencia_meses INTEGER NOT NULL DEFAULT 1,
    dias_envio TEXT NOT NULL DEFAULT '[]',
    ocurrencias_requeridas INTEGER NOT NULL DEFAULT 1,
    hora_limite TEXT,
    dias_plazo INTEGER,
    tipo_plazo TEXT,
    activo INTEGER NOT NULL DEFAULT 1,
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alias_reportes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporte_id INTEGER NOT NULL REFERENCES reportes(id),
    alias TEXT NOT NULL,
    alias_normalizado TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS obligaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporte_id INTEGER NOT NULL REFERENCES reportes(id),
    regla_id INTEGER NOT NULL REFERENCES reglas_reportes(id),
    periodo_clave TEXT NOT NULL,
    fecha_corte TEXT NOT NULL,
    fecha_inicio_envio TEXT,
    fecha_hora_limite TEXT,
    ocurrencia INTEGER NOT NULL DEFAULT 1,
    estado TEXT NOT NULL DEFAULT 'ABIERTO',
    ultima_revision TEXT,
    creada_en TEXT NOT NULL,
    actualizada_en TEXT NOT NULL,
    UNIQUE(regla_id, periodo_clave, ocurrencia)
);

CREATE TABLE IF NOT EXISTS ejecuciones_scraping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    iniciado_en TEXT NOT NULL,
    finalizado_en TEXT,
    fecha_inicio_consulta TEXT NOT NULL,
    fecha_fin_consulta TEXT NOT NULL,
    cantidad_resultados INTEGER NOT NULL DEFAULT 0,
    resultado TEXT NOT NULL,
    detalle_error TEXT
);

CREATE TABLE IF NOT EXISTS observaciones_reportes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ejecucion_id INTEGER NOT NULL REFERENCES ejecuciones_scraping(id),
    reporte_id INTEGER REFERENCES reportes(id),
    nombre_raw TEXT NOT NULL,
    nombre_normalizado TEXT NOT NULL,
    fecha_corte_raw TEXT,
    fecha_corte TEXT,
    fecha_llegada TEXT,
    tipo_entidad TEXT,
    sigla TEXT,
    email TEXT,
    resultado_raw TEXT,
    validacion TEXT,
    envio TEXT,
    estado TEXT NOT NULL,
    detalle TEXT,
    observado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS obligacion_observacion (
    obligacion_id INTEGER NOT NULL REFERENCES obligaciones(id),
    observacion_id INTEGER NOT NULL REFERENCES observaciones_reportes(id),
    PRIMARY KEY(obligacion_id, observacion_id)
);

CREATE TABLE IF NOT EXISTS incumplimientos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obligacion_id INTEGER REFERENCES obligaciones(id),
    reporte_id INTEGER REFERENCES reportes(id),
    nombre_snapshot TEXT NOT NULL,
    tipo_periodo TEXT NOT NULL,
    fecha_corte TEXT NOT NULL,
    ocurrencia INTEGER NOT NULL DEFAULT 1,
    detectado_en TEXT NOT NULL,
    resuelto_en TEXT,
    estado TEXT NOT NULL DEFAULT 'FALTANTE'
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_incumplimiento_obligacion
    ON incumplimientos(obligacion_id)
    WHERE obligacion_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS eventos_notificacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obligacion_id INTEGER REFERENCES obligaciones(id),
    tipo_evento TEXT NOT NULL,
    huella TEXT NOT NULL UNIQUE,
    enviado_en TEXT NOT NULL,
    resuelto_en TEXT,
    detalle TEXT
);

CREATE TABLE IF NOT EXISTS credenciales (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    usuario TEXT NOT NULL,
    password_protegida TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_obligaciones_fecha
    ON obligaciones(fecha_corte, estado);
CREATE INDEX IF NOT EXISTS ix_observaciones_fecha
    ON observaciones_reportes(fecha_corte, reporte_id);
CREATE INDEX IF NOT EXISTS ix_alias_normalizado
    ON alias_reportes(alias_normalizado);
"""


def resolve_path(path: str | Path) -> Path:
    """Resuelve paths relativos al directorio del proyecto."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path(__file__).resolve().parent / candidate
    return candidate


def local_now() -> datetime:
    """Obtiene la hora local de Bolivia para calcular vencimientos."""
    if _LOCAL_TZ:
        return datetime.now(_LOCAL_TZ)
    return datetime.now()


def now_iso(value: Optional[datetime] = None) -> str:
    value = value or local_now()
    return value.isoformat(timespec="seconds")


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = resolve_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


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


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None and _LOCAL_TZ:
        return value.replace(tzinfo=_LOCAL_TZ)
    if value.tzinfo is not None and _LOCAL_TZ is None:
        return value.replace(tzinfo=None)
    return value


def _protect_secret(value: str) -> str:
    raw = value.encode("utf-8")
    if os.name != "nt":
        return "plain:" + base64.b64encode(raw).decode("ascii")

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.c_uint32),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    input_buffer = ctypes.create_string_buffer(raw)
    input_blob = DataBlob(
        len(raw), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte))
    )
    output_blob = DataBlob()
    crypt32.CryptProtectData.restype = ctypes.c_bool
    ok = crypt32.CryptProtectData(
        ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
    return "dpapi:" + base64.b64encode(encrypted).decode("ascii")


def _unprotect_secret(value: str) -> str:
    if value.startswith("plain:"):
        return base64.b64decode(value[6:]).decode("utf-8")
    if not value.startswith("dpapi:"):
        raise ValueError("Formato de credencial no reconocido")
    if os.name != "nt":
        raise RuntimeError("La credencial DPAPI solo puede abrirse en Windows")

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.c_uint32),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    encrypted = base64.b64decode(value[6:])
    crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    input_buffer = ctypes.create_string_buffer(encrypted)
    input_blob = DataBlob(
        len(encrypted), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte))
    )
    output_blob = DataBlob()
    crypt32.CryptUnprotectData.restype = ctypes.c_bool
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        decrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
    return decrypted.decode("utf-8")


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
        elif version == 1:
            try:
                conn.execute("ALTER TABLE reglas_reportes ADD COLUMN mes_ancla INTEGER")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
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


def seed_catalog(conn: sqlite3.Connection, seed_path: str | Path) -> None:
    data = json.loads(resolve_path(seed_path).read_text(encoding="utf-8"))
    reports = data.get("reportes", data.get("reports", []))
    for item in reports:
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


def _insert_rule(
    conn: sqlite3.Connection, report_id: int, rule: dict, timestamp: Optional[str] = None
) -> int:
    timestamp = timestamp or now_iso()
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
           dias_plazo, tipo_plazo, activo, creado_en, actualizado_en)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
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


def _add_business_days(start: date, days: int) -> date:
    result = start
    remaining = max(0, int(days))
    while remaining:
        result += timedelta(days=1)
        if result.weekday() < 5:
            remaining -= 1
    return result


def _deadline_date(cutoff: date, days: Optional[int], tipo_plazo: Optional[str]) -> Optional[date]:
    if days is None:
        return None
    if str(tipo_plazo or "calendario").lower() in {"habil", "habiles", "hábil", "hábiles"}:
        return _add_business_days(cutoff, int(days))
    return cutoff + timedelta(days=int(days))


def calculate_obligation(rule: dict, today: date) -> Optional[dict]:
    """Calcula una obligación concreta para una regla y la fecha actual."""
    kind = str(rule.get("regla_fecha_corte", "AYER")).upper()
    cutoff = None
    window_start = None
    deadline_day = None

    if kind == "AYER":
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
            cutoff, rule.get("dias_plazo"), rule.get("tipo_plazo")
        )
        period_key = cutoff.strftime("%Y-%m") if frequency == 1 else cutoff.isoformat()
    else:
        return None

    deadline = _combine_date_time(deadline_day, rule.get("hora_limite")) if deadline_day else None
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
    created = []
    with conn:
        for rule in get_active_rules(conn):
            calculated = calculate_obligation(rule, today)
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


def get_query_date_range(
    conn: sqlite3.Connection, current: Optional[datetime] = None, lookback_days: int = 62
) -> tuple[date, date]:
    """Devuelve el único día de corte que se consulta en SCIP: ayer.

    ``lookback_days`` se conserva por compatibilidad con los llamadores
    existentes, pero la pantalla de SCIP siempre recibe ayer como inicio y
    final para evitar mezclar periodos de corte distintos.
    """
    current = current or local_now()
    today = current.date()
    yesterday = today - timedelta(days=1)
    ensure_obligations(conn, current)
    return yesterday, yesterday


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
    deadline = parse_date(obligation["fecha_hora_limite"])
    arrival = parse_date(row.get("fecha_llegada"))
    if arrival is None:
        # La fecha no está disponible en algunas respuestas de SCIP; no se
        # descarta el resultado para evitar falsos faltantes.
        return True
    if start is not None and arrival < start:
        return False
    if deadline is not None and arrival > deadline:
        return False
    return True


def evaluate_obligations(
    conn: sqlite3.Connection, reportes: list[dict], current: Optional[datetime] = None
) -> dict[str, list[str]]:
    current = current or local_now()
    # Evaluar solo las obligaciones del periodo vigente evita mezclar
    # faltantes historicos con la respuesta actual de SCIP.
    current_obligations = ensure_obligations(conn, current)
    grouped: dict[tuple[Optional[int], Optional[str]], list[dict]] = {}
    for reporte in reportes:
        report_id = lookup_report_id(conn, reporte.get("grupo", ""))
        cutoff = parse_date(reporte.get("fecha_corte"))
        if report_id is not None and cutoff is not None:
            grouped.setdefault((report_id, cutoff.isoformat()), []).append(reporte)

    candidate_ids = [row["id"] for row in current_obligations]
    candidate_conditions = []
    candidate_params = []
    if candidate_ids:
        placeholders = ",".join("?" for _ in candidate_ids)
        candidate_conditions.append(f"o.id IN ({placeholders})")
        candidate_params.extend(candidate_ids)
    for report_id, cutoff in grouped:
        candidate_conditions.append("(o.reporte_id = ? AND o.fecha_corte = ?)")
        candidate_params.extend((report_id, cutoff))

    candidates = []
    if candidate_conditions:
        candidates = conn.execute(
            f"""
            SELECT o.*, p.codigo, p.nombre, p.tipo_periodo
            FROM obligaciones o
            JOIN reportes p ON p.id = o.reporte_id
            JOIN reglas_reportes rr ON rr.id = o.regla_id AND rr.activo = 1
            WHERE p.activo = 1 AND p.validacion_activa = 1
              AND o.estado <> 'DESCARTADO'
              AND ({' OR '.join(candidate_conditions)})
            ORDER BY o.fecha_corte, p.codigo, o.ocurrencia
            """,
            candidate_params,
        ).fetchall()

    candidate_groups: dict[tuple[int, str], list[sqlite3.Row]] = {}
    for row in candidates:
        candidate_groups.setdefault((row["reporte_id"], row["fecha_corte"]), []).append(row)

    missing = {"diario": [], "semanal": [], "mensual": [], "otros": []}
    timestamp = now_iso(current)
    with conn:
        for key, obligation_rows in candidate_groups.items():
            rows_for_key = grouped.get(key, [])
            occurrence_map = _rows_by_occurrence(
                [
                    item
                    for item in rows_for_key
                    if _observation_in_window(item, obligation_rows[0], current)
                ],
                [dict(row) for row in obligation_rows],
            )
            for row in obligation_rows:
                occurrence_rows = occurrence_map.get(row["ocurrencia"], [])
                statuses = {item.get("estado") for item in occurrence_rows}
                if "EXITOSO" in statuses:
                    status = "EXITOSO"
                elif "ERROR" in statuses:
                    status = "ERROR"
                elif "PENDIENTE" in statuses:
                    status = "PENDIENTE"
                elif not row["fecha_hora_limite"]:
                    status = "CONFIGURAR"
                else:
                    deadline = datetime.fromisoformat(row["fecha_hora_limite"])
                    status = "FALTANTE" if _as_aware(current) > _as_aware(deadline) else "ABIERTO"

                previous = row["estado"]
                conn.execute(
                    "UPDATE obligaciones SET estado = ?, ultima_revision = ?, actualizada_en = ? WHERE id = ?",
                    (status, timestamp, timestamp, row["id"]),
                )

                if status == "FALTANTE":
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO incumplimientos
                          (obligacion_id, reporte_id, nombre_snapshot, tipo_periodo,
                           fecha_corte, ocurrencia, detectado_en, estado)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'FALTANTE')
                        """,
                        (
                            row["id"],
                            row["reporte_id"],
                            row["nombre"],
                            row["tipo_periodo"],
                            row["fecha_corte"],
                            row["ocurrencia"],
                            timestamp,
                        ),
                    )
                    tipo = row["tipo_periodo"] if row["tipo_periodo"] in missing else "otros"
                    missing[tipo].append(row["nombre"])
                elif status == "EXITOSO" and previous == "FALTANTE":
                    conn.execute(
                        """
                        UPDATE incumplimientos
                        SET resuelto_en = ?, estado = 'RESUELTO'
                        WHERE obligacion_id = ? AND resuelto_en IS NULL
                        """,
                        (timestamp, row["id"]),
                    )

    return missing


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
    condition = "WHERE i.resuelto_en IS NULL AND i.estado = 'FALTANTE'" if unresolved_only else ""
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
    """Obligaciones vencidas: sin envío exitoso y con hora límite ya superada.

    Devuelve además la fecha y la hora límite de envío por separado para
    mostrarlas en la interfaz ("debía enviarse" y "hora máxima").
    """
    current = current or local_now()
    today = current.date().isoformat()
    rows = conn.execute(
        """
        SELECT o.id, o.periodo_clave, o.fecha_corte, o.fecha_hora_limite,
               o.ocurrencia, o.estado, o.actualizada_en,
               p.codigo, p.nombre, p.tipo_periodo
        FROM obligaciones o
        JOIN reportes p ON p.id = o.reporte_id
        JOIN reglas_reportes rr ON rr.id = o.regla_id AND rr.activo = 1
        WHERE p.activo = 1 AND p.validacion_activa = 1
          AND o.fecha_corte < ?
          AND o.estado NOT IN ('EXITOSO', 'DESCARTADO')
          AND o.fecha_hora_limite IS NOT NULL
        ORDER BY o.fecha_hora_limite, p.codigo, o.ocurrencia
        """,
        (today,),
    ).fetchall()
    overdue: list[dict] = []
    for row in rows:
        try:
            deadline = datetime.fromisoformat(row["fecha_hora_limite"])
        except (TypeError, ValueError):
            continue
        if _as_aware(current) > _as_aware(deadline):
            item = dict(row)
            item["fecha_limite"] = deadline.date().isoformat()
            item["hora_limite"] = deadline.strftime("%H:%M")
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
