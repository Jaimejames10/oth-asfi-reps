#!/usr/bin/env python
"""Script de depuración para ver qué reportes se obtienen"""
import sys
import json
from asfi_monitor import obtener_reportes

reportes = obtener_reportes()

print("\n" + "="*70)
print("REPORTES OBTENIDOS")
print("="*70)

for i, r in enumerate(reportes, 1):
    print(f"\n{i}. GRUPO: {r['grupo']}")
    print(f"   Estado: {r['estado']}")
    print(f"   Envío/Reproceso: {r['envio']}")
    print(f"   Validación: {r['validacion'][:50] if r['validacion'] else '(vacío)'}")

print(f"\n{'='*70}")
print(f"TOTAL: {len(reportes)} reportes obtenidos")
print(f"{'='*70}\n")

# Guardar a JSON para referencia
with open("reportes_debug.json", "w", encoding="utf-8") as f:
    json.dump([{
        "grupo": r["grupo"],
        "estado": r["estado"],
        "envio": r["envio"],
        "validacion": r["validacion"]
    } for r in reportes], f, indent=2, ensure_ascii=False)
print("✓ Datos guardados en reportes_debug.json")
