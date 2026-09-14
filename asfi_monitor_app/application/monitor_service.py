"""
ASFI SCIP Monitor - Monitor de Control de Envíos
=================================================
Monitorea reportes enviados al sistema ASFI/SCIP y notifica
via Windows Toast si detecta errores o fallos en los envíos.

Dependencias:
    pip install playwright plyer schedule
    playwright install chromium

Configuración:
    python gestionar_reportes.py

Uso:
    python asfi_monitor.py
    python asfi_monitor.py --intervalo 10   # revisar cada 10 minutos
    python asfi_monitor.py --una-vez        # ejecutar solo una vez
"""

from __future__ import annotations

import io
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from asfi_monitor_app.config import CONFIG
from asfi_monitor_app.domain.analysis import (
    _es_error_validacion,
    _normalizar_texto,
    analizar_reporte_nuevo,
    reconciliar_reportes_subsanados,
)
from asfi_monitor_app.integrations.notifications import notificar as _notificar
from asfi_monitor_app.integrations.scip_client import (
    _ingresar_fecha,
    _leer_texto_celda,
    _obtener_indices_columnas,
    obtener_reportes as _obtener_reportes,
    obtener_reportes_por_rangos as _obtener_reportes_por_rangos,
)
from asfi_monitor_app.storage.state import (
    cargar_estado as _cargar_estado,
    clave_reporte as _clave_reporte,
    guardar_estado as _guardar_estado,
)
from asfi_monitor_app.storage import api as reportes_db

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = PROJECT_ROOT


def ruta_archivo(config_key: str) -> Path:
    """Resuelve un archivo de configuración relativo al proyecto."""
    return reportes_db.resolve_path(CONFIG[config_key])


# Ruta absoluta del ícono de notificaciones (relativa a este script y no al CWD,
# para que funcione también en la tarea programada de Windows)
RUTA_ICONO = ruta_archivo("icono_notificacion")
if not RUTA_ICONO.exists():
    RUTA_ICONO = None  # Sin logo disponible: notificaciones con ícono por defecto


# ──────────────────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────────────────
# Configurar stdout con encoding UTF-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "asfi_monitor.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("asfi_monitor")


def notificar(titulo: str, mensaje: str, urgente: bool = False) -> None:
    _notificar(titulo, mensaje, urgente, logger=log, icon_path=RUTA_ICONO)


def cargar_estado() -> dict:
    return _cargar_estado(ruta_archivo("archivo_estado"))


def guardar_estado(estado: dict) -> None:
    _guardar_estado(ruta_archivo("archivo_estado"), estado)


def clave_reporte(reporte: dict) -> str:
    """Genera clave única para un reporte (para deduplicar notificaciones)."""
    return _clave_reporte(reporte)


# ──────────────────────────────────────────────────────────────────────────────
# VALIDACIÓN COMPATIBLE CON EL CATÁLOGO SQLITE
# ──────────────────────────────────────────────────────────────────────────────
def obtener_dia_semana(fecha_str: str) -> str:
    """Mantiene la utilidad anterior para consumidores externos."""
    fecha_obj = reportes_db.parse_date(fecha_str)
    if fecha_obj is None:
        log.warning(f"No se pudo determinar el día de la semana para {fecha_str}")
        return None
    dias = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    return dias[fecha_obj.weekday()]


def _validar_tipo_desde_db(reportes: list[dict], tipo_periodo: str) -> list[str]:
    """Evalúa obligaciones SQLite y devuelve faltantes del tipo solicitado."""
    db_path = ruta_archivo("archivo_base_datos")
    reportes_db.initialize_database(
        db_path, ruta_archivo("archivo_semilla"), ruta_archivo("archivo_no_enviados")
    )
    conn = reportes_db.connect(db_path)
    try:
        resultado = reportes_db.evaluate_obligations(conn, reportes, reportes_db.local_now())
        return resultado.get(tipo_periodo, [])
    finally:
        conn.close()


def validar_reportes_dia(reportes: list[dict], fecha_consulta: str) -> list[str]:
    """Compatibilidad: valida los reportes diarios configurados en SQLite."""
    return _validar_tipo_desde_db(reportes, "diario")


def validar_reportes_semanales(reportes: list[dict], fecha_consulta: str) -> list[str]:
    """Compatibilidad: valida los reportes semanales configurados en SQLite."""
    return _validar_tipo_desde_db(reportes, "semanal")


def validar_reportes_mensuales(reportes: list[dict], fecha_consulta: str) -> list[str]:
    """Compatibilidad: valida reportes mensuales cuando estén activados."""
    return _validar_tipo_desde_db(reportes, "mensual")


def analizar_resultado(texto: str) -> tuple[str, str]:
    """Mantiene el analizador legacy con la configuracion activa."""
    from asfi_monitor_app.domain.analysis import analizar_resultado as _analizar_resultado

    return _analizar_resultado(texto, CONFIG)


def obtener_reportes(
    fecha_inicio: Optional[date] = None, fecha_fin: Optional[date] = None
) -> list[dict]:
    """Compatibilidad para el cliente Playwright separado."""
    return _obtener_reportes(
        fecha_inicio,
        fecha_fin,
        config=CONFIG,
        logger=log,
        notify=notificar,
        icon_path=RUTA_ICONO,
    )


def obtener_reportes_por_rangos(rangos: list[tuple[date, date]]) -> list[dict]:
    """Compatibilidad para consultas de varios rangos."""
    return _obtener_reportes_por_rangos(
        rangos,
        config=CONFIG,
        logger=log,
        notify=notificar,
        icon_path=RUTA_ICONO,
    )


def filtrar_reportes_para_revision(
    conn,
    reportes: list[dict],
    fecha_fin: date,
    active_period_types: dict[str, set[str]],
    consulta_manual: bool = False,
) -> list[dict]:
    """Conserva solo filas del día actual o del tipo activo para cada corte."""
    if consulta_manual:
        return list(reportes)

    result = []
    for reporte in reportes:
        cutoff = reportes_db.parse_date(reporte.get("fecha_corte"))
        if cutoff is None:
            continue
        if cutoff == fecha_fin:
            result.append(reporte)
            continue
        tipo_periodo = reportes_db.lookup_report_period(
            conn, reporte.get("grupo", "")
        )
        if tipo_periodo in active_period_types.get(cutoff.isoformat(), set()):
            result.append(reporte)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# LÓGICA PRINCIPAL DE MONITOREO
# ──────────────────────────────────────────────────────────────────────────────
def inicializar_base_datos() -> Path:
    """Crea/aplica la base y carga el catálogo inicial si todavía está vacía."""
    return reportes_db.initialize_database(
        ruta_archivo("archivo_base_datos"),
        ruta_archivo("archivo_semilla"),
        ruta_archivo("archivo_no_enviados"),
    )


def cargar_credenciales_desde_db(db_path: Optional[Path] = None) -> tuple[str, str]:
    """Lee usuario y contraseña desde SQLite sin escribirlos en logs."""
    db_path = db_path or inicializar_base_datos()
    conn = reportes_db.connect(db_path)
    try:
        return reportes_db.get_credentials(conn)
    finally:
        conn.close()


def cargar_configuracion_desde_db(db_path: Optional[Path] = None) -> None:
    """Carga desde SQLite las opciones editables en la interfaz gráfica."""
    db_path = db_path or inicializar_base_datos()
    conn = reportes_db.connect(db_path)
    try:
        url_base = reportes_db.get_app_setting(conn, "monitor_url_base")
        if url_base:
            CONFIG["url_base"] = url_base.rstrip("/")

        if not CONFIG.get("_intervalo_fijado_cli", False):
            intervalo = reportes_db.get_app_setting(conn, "monitor_intervalo_minutos")
            if intervalo:
                try:
                    intervalo_numero = int(intervalo)
                    if intervalo_numero > 0:
                        CONFIG["intervalo_minutos"] = intervalo_numero
                except ValueError:
                    log.warning("Se ignoró un intervalo inválido guardado en SQLite")

        CONFIG["fecha_inicio_corte"] = reportes_db.get_app_setting(
            conn, "monitor_fecha_inicio_corte"
        )
        CONFIG["fecha_fin_corte"] = reportes_db.get_app_setting(
            conn, "monitor_fecha_fin_corte"
        )
    finally:
        conn.close()


def ejecutar_revision() -> None:
    """Revisa los reportes y envía notificaciones si hay problemas."""
    log.info("=" * 60)
    current = reportes_db.local_now()
    log.info(f"Iniciando revisión: {current.strftime('%Y-%m-%d %H:%M:%S')}")

    db_path = inicializar_base_datos()
    cargar_configuracion_desde_db(db_path)

    if CONFIG.get("_usar_credenciales_db", True):
        try:
            CONFIG["usuario"], CONFIG["password"] = cargar_credenciales_desde_db(db_path)
        except Exception as exc:
            log.error(f"No se pudieron actualizar las credenciales desde SQLite: {exc}")
            return

    estado = cargar_estado()
    estado["ultima_revision"] = reportes_db.now_iso(current)

    conn = reportes_db.connect(db_path)
    reportes_db.ensure_obligations(conn, current)
    rangos_consulta = reportes_db.get_query_date_ranges(
        conn,
        current,
        configured_start=CONFIG.get("fecha_inicio_corte"),
        configured_end=CONFIG.get("fecha_fin_corte"),
    )
    fecha_inicio = min(start for start, _end in rangos_consulta)
    fecha_fin = max(end for _start, end in rangos_consulta)
    log.info(
        "Rangos de consulta: %s",
        ", ".join(
            f"{reportes_db.format_asfi_date(start)} a {reportes_db.format_asfi_date(end)}"
            for start, end in rangos_consulta
        ),
    )
    run_id = reportes_db.start_scrape_run(conn, fecha_inicio, fecha_fin)

    try:
        reportes = obtener_reportes_por_rangos(rangos_consulta)
    except Exception as exc:
        reportes_db.finish_scrape_run(conn, run_id, "ERROR", 0, str(exc)[:500])
        conn.close()
        log.error(f"Fallo crítico obteniendo reportes: {exc}", exc_info=True)
        notificar(
            "🔴 ASFI/SCIP Monitor - Fallo crítico",
            f"Error obteniendo reportes: {str(exc)[:200]}",
            urgente=True,
        )
        guardar_estado(estado)
        return

    sin_enviar_diarios: list[dict] = []
    sin_enviar_semanales: list[dict] = []
    try:
        revision_actual = reportes_db.local_now()
        obligaciones_previas = reportes_db.list_current_obligations(conn)
        active_period_types: dict[str, set[str]] = {}
        for fila in obligaciones_previas:
            if fila["tipo_periodo"] not in ("semanal", "mensual"):
                continue
            inicio = reportes_db.parse_date(fila.get("fecha_inicio_envio"))
            limite = reportes_db.parse_datetime(fila.get("fecha_hora_limite"))
            if (
                inicio is not None
                and limite is not None
                and revision_actual.date() >= inicio
                and revision_actual <= limite
            ):
                active_period_types.setdefault(fila["fecha_corte"], set()).add(
                    fila["tipo_periodo"]
                )

        reportes_revision = filtrar_reportes_para_revision(
            conn,
            reportes,
            fecha_fin,
            active_period_types,
            consulta_manual=bool(
                CONFIG.get("fecha_inicio_corte") and CONFIG.get("fecha_fin_corte")
            ),
        )

        if not reportes:
            log.warning(
                "No se obtuvieron reportes; se evaluarán las obligaciones con el historial guardado"
            )
        if len(reportes_revision) != len(reportes):
            log.info(
                "Filas fuera de las obligaciones vigentes ignoradas: %s",
                len(reportes) - len(reportes_revision),
            )
        ocurrencias_por_reporte = reportes_db.get_required_occurrence_counts(
            conn, reportes_revision
        )
        reconciliar_reportes_subsanados(reportes_revision, ocurrencias_por_reporte)
        reportes_db.store_observations(conn, run_id, reportes_revision)

        evaluacion = reportes_db.evaluate_obligations(
            conn, reportes_revision, revision_actual
        )
        obligaciones_actuales = reportes_db.list_current_obligations(conn)

        # Diarios del período vigente que aún no se registran como enviados
        # (ABIERTO/PENDIENTE): se avisan en cada ciclo mientras esté dentro
        # del plazo; al vencer pasan a FALTANTE y usan la alerta urgente.
        sin_enviar_diarios = [
            fila for fila in obligaciones_actuales
            if fila["tipo_periodo"] == "diario"
            and fila["estado"] in ("ABIERTO", "PENDIENTE")
        ]
        for fila in obligaciones_actuales:
            if fila["tipo_periodo"] != "semanal" or fila["estado"] not in ("ABIERTO", "PENDIENTE"):
                continue
            inicio = reportes_db.parse_date(fila.get("fecha_inicio_envio"))
            limite = reportes_db.parse_datetime(fila.get("fecha_hora_limite"))
            if inicio is not None and limite is not None:
                if revision_actual.date() >= inicio and revision_actual <= limite:
                    sin_enviar_semanales.append(fila)
        reportes_db.finish_scrape_run(
            conn,
            run_id,
            "VACIO" if not reportes_revision else "OK",
            len(reportes_revision),
        )
    except Exception as exc:
        reportes_db.finish_scrape_run(conn, run_id, "ERROR", len(reportes), str(exc)[:500])
        conn.close()
        log.error(f"Fallo guardando resultados en SQLite: {exc}", exc_info=True)
        notificar(
            "🔴 ASFI/SCIP Monitor - Error de persistencia",
            f"No se pudieron guardar los resultados: {str(exc)[:200]}",
            urgente=True,
        )
        guardar_estado(estado)
        return
    finally:
        conn.close()

    fecha_corte_ayer = reportes_db.local_now().date() - timedelta(days=1)
    fmt_fecha = reportes_db.format_asfi_date(fecha_corte_ayer)

    errores = []
    pendientes = []
    exitosos = []

    for r in reportes_revision:
        clave = clave_reporte(r)

        if r["estado"] == "ERROR":
            errores.append(r)
            # SIEMPRE notificar errores - sin antispam
            # (así se envía alerta cada ejecución hasta que se corrija)
            estado["alertas_enviadas"][clave] = {
                "estado": r["estado"],
                "notificado": datetime.now().isoformat(),
            }
            # Notificación URGENTE para errores
            titulo = f"🔴 ERROR en reporte ASFI — {r['grupo'][:40]}"
            msg = (
                f"Entidad: {r['sigla']} | Corte: {r['fecha_corte']}\n"
                f"Grupo: {r['grupo'][:60]}\n"
                f"Problema: {r['detalle'][:100]}\n"
                f"Envío: {r['envio']}"
            )
            notificar(titulo, msg, urgente=True)
            log.warning(f"ALERTA ENVIADA: {titulo}")

        elif r["estado"] == "PENDIENTE":
            pendientes.append(r)

        elif r["estado"] == "EXITOSO":
            exitosos.append(r)
            # Limpiar alerta si antes estaba en error y ahora es exitoso
            # (notificar resolución del problema)
            if clave in estado["alertas_enviadas"]:
                prev = estado["alertas_enviadas"][clave]
                if prev.get("estado") == "ERROR":
                    del estado["alertas_enviadas"][clave]
                    notificar(
                        f"✅ Reporte ASFI resuelto — {r['grupo'][:40]}",
                        f"El reporte '{r['grupo'][:60]}' ahora figura como exitoso.",
                    )

    # Validar que se enviaron todos los reportes esperados para el día
    log.info(f"Validando obligaciones configuradas para {fmt_fecha}...")
    reportes_faltantes_diarios = evaluacion.get("diario", [])
    reportes_faltantes_semanales = evaluacion.get("semanal", [])
    reportes_faltantes_mensuales = evaluacion.get("mensual", [])
    reportes_tardios_mensuales = evaluacion.get("tardios", {}).get("mensual", [])

    if reportes_faltantes_diarios:
        log.warning(f"⚠️  Reportes diarios FALTANTES: {reportes_faltantes_diarios}")
        clave_faltantes = f"faltantes_diarios|{fmt_fecha}"
        # SIEMPRE notificar reportes faltantes - sin antispam
        # (así se envía alerta cada ejecución hasta que se envíen)
        estado["alertas_enviadas"][clave_faltantes] = {
            "estado": "FALTANTE",
            "notificado": datetime.now().isoformat(),
        }
        listado = "\n".join(f"  • {r}" for r in reportes_faltantes_diarios[:10])
        if len(reportes_faltantes_diarios) > 10:
            listado += f"\n  ...y {len(reportes_faltantes_diarios) - 10} más"
        notificar(
            f"⚠️ ASFI/SCIP Monitor - {len(reportes_faltantes_diarios)} reportes FALTANTES",
            f"Fecha: {fmt_fecha}\n\n{listado}",
            urgente=True,
        )

    if reportes_faltantes_semanales:
        log.warning(f"⚠️ Reportes semanales VENCIDOS: {reportes_faltantes_semanales}")
        clave_faltantes = f"vencidos_semanales|{fmt_fecha}"
        # SIEMPRE notificar reportes semanales faltantes - sin antispam
        # (así se envía alerta cada ejecución hasta que se envíen)
        estado["alertas_enviadas"][clave_faltantes] = {
            "estado": "VENCIDO_SIN_ENVIAR",
            "notificado": datetime.now().isoformat(),
        }
        listado = "\n".join(f"  • {r}" for r in reportes_faltantes_semanales)
        notificar(
            f"⚠️ ASFI/SCIP Monitor - {len(reportes_faltantes_semanales)} reportes SEMANALES VENCIDOS",
            f"Fecha límite: lunes 12:00\n\n{listado}",
            urgente=True,
        )

    if reportes_faltantes_mensuales:
        log.warning(f"⚠️ Reportes mensuales FALTANTES: {reportes_faltantes_mensuales}")
        clave_faltantes = f"faltantes_mensuales|{fmt_fecha}"
        estado["alertas_enviadas"][clave_faltantes] = {
            "estado": "FALTANTE",
            "notificado": reportes_db.now_iso(),
        }
        listado = "\n".join(f"  • {r}" for r in reportes_faltantes_mensuales)
        notificar(
            f"⚠️ ASFI/SCIP Monitor - {len(reportes_faltantes_mensuales)} reportes MENSUALES FALTANTES",
            f"Fecha de corte: {fmt_fecha}\n\n{listado}",
            urgente=True,
        )

    if reportes_tardios_mensuales:
        log.warning(
            "⚠️ Reportes mensuales enviados fuera de plazo: %s",
            reportes_tardios_mensuales,
        )
        clave_tardios = f"tardios_mensuales|{fmt_fecha}"
        estado["alertas_enviadas"][clave_tardios] = {
            "estado": "EXITOSO_TARDIO",
            "notificado": reportes_db.now_iso(),
        }
        listado = "\n".join(f"  • {nombre}" for nombre in reportes_tardios_mensuales)
        notificar(
            f"⚠️ ASFI/SCIP Monitor - {len(reportes_tardios_mensuales)} reportes MENSUALES TARDÍOS",
            f"El envío figura como exitoso, pero llegó después de la fecha/hora límite.\n\n{listado}",
            urgente=True,
        )

    # Recordatorio periódico (cada ciclo, por defecto 15 min) de los reportes
    # diarios del día que todavía no se han enviado y siguen dentro del plazo.
    if sin_enviar_diarios:
        nombres_sin_enviar = [
            f"{fila['nombre']}" + (f" (envío {fila['ocurrencia']})" if fila["ocurrencia"] > 1 else "")
            for fila in sin_enviar_diarios
        ]
        log.info(f"Reportes diarios aún sin enviar (dentro de plazo): {len(nombres_sin_enviar)}")
        clave_sin_enviar = f"sin_enviar_diarios|{fmt_fecha}"
        estado["alertas_enviadas"][clave_sin_enviar] = {
            "estado": "SIN_ENVIAR",
            "notificado": reportes_db.now_iso(),
        }
        listado = "\n".join(f"  • {nombre}" for nombre in nombres_sin_enviar[:10])
        if len(nombres_sin_enviar) > 10:
            listado += f"\n  ...y {len(nombres_sin_enviar) - 10} más"
        limite = sin_enviar_diarios[0].get("fecha_hora_limite") or "-"
        notificar(
            f"⏳ ASFI/SCIP Monitor - {len(nombres_sin_enviar)} reportes diarios sin enviar",
            f"Corte: {fmt_fecha}\nLímite de envío: {str(limite).replace('T', ' ')[:19]}\n\n{listado}",
            urgente=False,
        )

    # Recordatorio periódico de los semanales durante la ventana de envío.
    if sin_enviar_semanales:
        nombres_sin_enviar = [
            f"{fila['nombre']} (corte {fila['fecha_corte']})"
            + (f" (envío {fila['ocurrencia']})" if fila["ocurrencia"] > 1 else "")
            for fila in sin_enviar_semanales
        ]
        log.info(
            "Reportes semanales aún sin cerrar (dentro de plazo): %s",
            len(nombres_sin_enviar),
        )
        limite = sin_enviar_semanales[0].get("fecha_hora_limite") or "-"
        listado = "\n".join(f"  • {nombre}" for nombre in nombres_sin_enviar[:10])
        if len(nombres_sin_enviar) > 10:
            listado += f"\n  ...y {len(nombres_sin_enviar) - 10} más"
        notificar(
            f"⏳ ASFI/SCIP Monitor - {len(nombres_sin_enviar)} reportes semanales pendientes",
            f"Límite de envío: {str(limite).replace('T', ' ')[:19]}\n\n{listado}",
            urgente=False,
        )

    # Listado en consola del estado de los reportes
    total_faltantes = (
        len(reportes_faltantes_diarios)
        + len(reportes_faltantes_semanales)
        + len(reportes_faltantes_mensuales)
    )
    log.info(
        f"Estado de los reportes "
        f"({len(exitosos)} ✅ | {len(errores)} ❌ | "
        f"{total_faltantes} ⚠️ | "
        f"{len(reportes_tardios_mensuales)} ⏱️ | "
        f"{len(sin_enviar_diarios) + len(sin_enviar_semanales)} ⏳):"
    )
    for r in exitosos:
        log.info(f"  ✅ {r['grupo']} ({r['sigla']} | corte {r['fecha_corte']})")
    for nombre in reportes_faltantes_diarios:
        log.info(f"  ⚠️ {nombre} (FALTANTE)")
    for fila in sin_enviar_diarios:
        sufijo = f" (envío {fila['ocurrencia']})" if fila["ocurrencia"] > 1 else ""
        log.info(f"  ⏳ {fila['nombre']}{sufijo} (SIN ENVIAR - dentro de plazo)")
    for fila in sin_enviar_semanales:
        sufijo = f" (envío {fila['ocurrencia']})" if fila["ocurrencia"] > 1 else ""
        log.info(
            f"  ⏳ {fila['nombre']}{sufijo} "
            f"(SEMANAL SIN ENVIAR - corte {fila['fecha_corte']})"
        )
    for nombre in reportes_faltantes_semanales:
        log.info(f"  ⚠️ {nombre} (SEMANAL VENCIDO SIN ENVIAR)")
    for nombre in reportes_faltantes_mensuales:
        log.info(f"  ⚠️ {nombre} (MENSUAL FALTANTE)")
    for nombre in reportes_tardios_mensuales:
        log.info(f"  ⏱️ {nombre} (MENSUAL ENVIADO FUERA DE PLAZO)")
    for r in errores:
        log.info(f"  ❌ {r['grupo']} ({r['sigla']} | corte {r['fecha_corte']}) - {r['detalle'][:60]}")
    if not exitosos:
        log.warning("Ningún reporte figura como aceptado correctamente")

    # Resumen en log
    log.info(
        f"Resumen: {len(exitosos)} exitosos | "
        f"{len(pendientes)} pendientes | "
        f"{len(errores)} errores | "
        f"{total_faltantes} faltantes | "
        f"{len(reportes_tardios_mensuales)} tardíos | "
        f"{len(sin_enviar_diarios) + len(sin_enviar_semanales)} sin enviar (dentro de plazo)"
    )

    # Notificación de resumen SOLO si hay errores múltiples
    if len(errores) > 1:
        grupos_error = "\n".join(f"• {r['grupo'][:50]}" for r in errores[:5])
        if len(errores) > 5:
            grupos_error += f"\n...y {len(errores) - 5} más"
        notificar(
            f"🔴 ASFI/SCIP Monitor - {len(errores)} reportes con ERROR",
            grupos_error,
            urgente=True,
        )

    guardar_estado(estado)
    log.info(f"Revisión completada. Próxima en {CONFIG['intervalo_minutos']} min.")


# ──────────────────────────────────────────────────────────────────────────────
# EXPORTAR REPORTE A CSV (utilidad)
# ──────────────────────────────────────────────────────────────────────────────
def exportar_csv(reportes: list[dict], archivo: str = "asfi_reportes.csv") -> None:
    import csv
    if not reportes:
        return
    campos = list(reportes[0].keys())
    with open(archivo, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(reportes)
    log.info(f"Exportado a {archivo}")
