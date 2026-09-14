"""Configuracion tecnica del monitor y valores por defecto."""

from __future__ import annotations

import os


CONFIG = {
    "url_base": "https://appweb.asfi.gob.bo/SCIP",
    "usuario": os.environ.get("ASFI_USUARIO", ""),
    "password": os.environ.get("ASFI_PASSWORD", ""),
    "dias_atras": 1,
    "intervalo_minutos": 15,
    "fecha_inicio_corte": None,
    "fecha_fin_corte": None,
    "palabras_exito": [
        "proceso finalizado",
        "validado saldos",
        "archivo recibido",
        "capturados correctamente",
        "registro realizado",
        "enviado correctamente",
        "recibido correctamente",
    ],
    "palabras_error": [
        "error",
        "fallo",
        "fallido",
        "rechazado",
        "no válido",
        "no valido",
        "inválido",
        "invalido",
        "diferencia:",
        "no se pudo",
        "exception",
        "timeout",
    ],
    "diferencia_maxima": 0,
    "archivo_estado": "asfi_estado.json",
    "archivo_no_enviados": "reportes_no_enviados.json",
    "archivo_base_datos": "asfi_monitor.db",
    "archivo_semilla": "reportes_seed.json",
    "headless": True,
    "timeout_segundos": 45,
    "icono_notificacion": "assets/asfi.ico",
}
