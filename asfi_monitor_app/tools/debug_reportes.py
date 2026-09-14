#!/usr/bin/env python
"""Herramienta de depuracion para inspeccionar reportes obtenidos."""

from __future__ import annotations

import json
import sys


def main() -> None:
    from asfi_monitor import CONFIG, cargar_credenciales_desde_db, obtener_reportes

    usuario, password = cargar_credenciales_desde_db()
    if not CONFIG["usuario"]:
        CONFIG["usuario"] = usuario
    if not CONFIG["password"]:
        CONFIG["password"] = password

    if not CONFIG["usuario"] or not CONFIG["password"]:
        print("No hay credenciales configuradas. Ejecuta gestionar_reportes.py primero.")
        sys.exit(1)

    reportes = obtener_reportes()
    print("\n" + "=" * 70)
    print("REPORTES OBTENIDOS")
    print("=" * 70)
    for index, reporte in enumerate(reportes, 1):
        print(f"\n{index}. GRUPO: {reporte['grupo']}")
        print(f"   Estado: {reporte['estado']}")
        print(f"   Envío/Reproceso: {reporte['envio']}")
        print(
            "   Validación: "
            f"{reporte['validacion'][:50] if reporte['validacion'] else '(vacío)'}"
        )

    print(f"\n{'=' * 70}")
    print(f"TOTAL: {len(reportes)} reportes obtenidos")
    print(f"{'=' * 70}\n")
    with open("reportes_debug.json", "w", encoding="utf-8") as output:
        json.dump(
            [
                {
                    "grupo": reporte["grupo"],
                    "estado": reporte["estado"],
                    "envio": reporte["envio"],
                    "validacion": reporte["validacion"],
                }
                for reporte in reportes
            ],
            output,
            indent=2,
            ensure_ascii=False,
        )
    print("Datos guardados en reportes_debug.json")
