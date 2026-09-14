"""Analisis de resultados ASFI independiente de Playwright y Tkinter."""

from __future__ import annotations

import logging
import re
from typing import Optional

from asfi_monitor_app.storage import api as reportes_db


def _normalizar_texto(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _es_error_validacion(value: str) -> bool:
    return _normalizar_texto(value) in {"error", "detalle error"}


def reconciliar_reportes_subsanados(
    reportes: list[dict],
    ocurrencias_por_reporte: Optional[dict[tuple[str, str], int]] = None,
) -> list[dict]:
    """Convierte en exitosos errores corregidos por otro envio equivalente."""
    log = logging.getLogger("asfi_monitor")
    ocurrencias_por_reporte = ocurrencias_por_reporte or {}
    grupos: dict[tuple[str, Optional[str], Optional[int]], list[dict]] = {}
    for reporte in reportes:
        nombre = reportes_db.normalize_name(str(reporte.get("grupo") or ""))
        if not nombre:
            continue
        cutoff = reportes_db.parse_date(reporte.get("fecha_corte"))
        cutoff_iso = cutoff.isoformat() if cutoff else None
        ocurrencias = ocurrencias_por_reporte.get((nombre, cutoff_iso), 1)
        numero_envio = reportes_db.extract_occurrence(reporte.get("envio", ""))
        if ocurrencias > 1 and numero_envio is None:
            continue

        clave = (nombre, cutoff_iso, numero_envio if ocurrencias > 1 else None)
        grupos.setdefault(clave, []).append(reporte)

    for (nombre, cutoff_iso, numero_envio), reportes_mismo_grupo in grupos.items():
        if len(reportes_mismo_grupo) < 2:
            continue
        reporte_exitoso = next(
            (
                reporte
                for reporte in reportes_mismo_grupo
                if reporte.get("estado") == "EXITOSO"
            ),
            None,
        )
        if reporte_exitoso is None:
            continue

        envio_exitoso = str(reporte_exitoso.get("envio") or "").strip()
        for reporte in reportes_mismo_grupo:
            if reporte.get("estado") != "ERROR":
                continue
            detalle_original = str(reporte.get("detalle") or "").strip()
            if numero_envio is None:
                detalle = "Subsanado por otro reporte exitoso del mismo nombre"
            else:
                detalle = "Subsanado por otro envío exitoso de la misma ocurrencia"
            if envio_exitoso:
                detalle += f" ({envio_exitoso})"
            if detalle_original:
                detalle += f". Error original: {detalle_original}"

            reporte["estado"] = "EXITOSO"
            reporte["detalle"] = detalle
            log.info(
                "Reporte subsanado por duplicado exitoso: %s (%s, ocurrencia=%s)",
                reporte.get("grupo", ""),
                cutoff_iso or reporte.get("fecha_corte", ""),
                numero_envio or "única",
            )
    return reportes


def analizar_reporte_nuevo(validacion: str, envio: str) -> tuple[str, str]:
    """Determina el estado usando las columnas de validacion y envio."""
    envio_lower = envio.lower() if envio else ""
    if _es_error_validacion(validacion):
        return "ERROR", f"Validación: {validacion.strip()[:100]}"
    if "en proceso de recepción" in envio_lower:
        return "PENDIENTE", "En proceso de recepción"
    if envio_lower.startswith("envío"):
        return "EXITOSO", f"{envio.strip()}"
    if envio.strip() and not _es_error_validacion(validacion):
        return "EXITOSO", f"Envío: {envio.strip()[:100]}"
    return "DESCONOCIDO", f"Validación: {validacion.strip()[:50]} | Envío: {envio.strip()[:50]}"


def analizar_resultado(texto: str, config: dict) -> tuple[str, str]:
    """Analiza el formato legacy usando la configuracion existente."""
    if not texto or not texto.strip():
        return "DESCONOCIDO", "Sin resultado registrado"

    texto_lower = texto.lower()
    for palabra in config["palabras_error"]:
        if palabra not in texto_lower:
            continue
        if palabra == "diferencia:":
            match = re.search(r"diferencia:\s*(\d+)", texto_lower)
            if match:
                diff = int(match.group(1))
                if diff <= config["diferencia_maxima"]:
                    continue
                return "ERROR", f"Diferencia de tamaño detectada: {diff}"
        else:
            for linea in texto.split("\n"):
                if palabra in linea.lower():
                    return "ERROR", linea.strip()[:120]
            return "ERROR", f"Palabra clave de error: '{palabra}'"

    for palabra in config["palabras_exito"]:
        if palabra in texto_lower:
            if "capturados correctamente" in texto_lower:
                return "PENDIENTE", "Archivos capturados, en proceso de validación en ASFI"
            return "EXITOSO", "Proceso completado correctamente"
    return "DESCONOCIDO", texto.strip()[:120]
