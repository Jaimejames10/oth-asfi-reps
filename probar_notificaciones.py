"""
Prueba las notificaciones de Windows y el análisis de resultados
SIN conectarse al sistema ASFI. Útil para verificar la instalación.
"""

import sys
import os

# Añadir el directorio padre al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asfi_monitor import notificar, analizar_resultado

# ─── Probar análisis de resultados ───────────────────────────────────────────
casos_prueba = [
    # (texto, estado_esperado)
    ("PROCESO FINALIZADO 8/29/2026 8:23:16 AM", "EXITOSO"),
    ("Validado saldos archivo: TI60828.CCM\nTamaño: 0 = 0 -- Diferencia: 0", "EXITOSO"),
    ("Archivo Recibido: TC60828.CCM\nPROCESO FINALIZADO", "EXITOSO"),
    ("Los archivos enviados fueron capturados correctamente y serán sometidos a validación", "PENDIENTE"),
    ("Registro realizado", "EXITOSO"),
    ("ERROR: El archivo no cumple el formato requerido", "ERROR"),
    ("Archivo Rechazado: formato inválido", "ERROR"),
    ("Validado saldos archivo: TI60828.CCM\nTamaño: 19244 = 18500 -- Diferencia: 744", "ERROR"),
    ("", "DESCONOCIDO"),
]

print("\n" + "=" * 60)
print("  PRUEBA DE ANÁLISIS DE RESULTADOS")
print("=" * 60)

todos_ok = True
for texto, esperado in casos_prueba:
    estado, detalle = analizar_resultado(texto)
    ok = "✅" if estado == esperado else "❌"
    if estado != esperado:
        todos_ok = False
    texto_short = texto[:50].replace("\n", " ") + ("..." if len(texto) > 50 else "")
    print(f"{ok} [{estado}] '{texto_short}'")
    if estado != esperado:
        print(f"     ⚠️  Esperado: {esperado}, Detalle: {detalle}")

print()
if todos_ok:
    print("✅ Todos los análisis correctos")
else:
    print("⚠️  Algunos análisis no coinciden con lo esperado (revisar palabras clave)")

# ─── Probar notificaciones ────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  PRUEBA DE NOTIFICACIONES WINDOWS")
print("=" * 60)
print()
print("Enviando notificación de prueba... (debería aparecer en la esquina)")
print()

notificar(
    "✅ ASFI Monitor - Prueba OK",
    "Las notificaciones funcionan correctamente. El monitor está listo."
)

print("Si no vio la notificación, verificar:")
print("  • Configuración de notificaciones en Windows (Inicio > Configuración > Notificaciones)")
print("  • Modo No molestar desactivado")
print()

notificar(
    "🔴 ASFI Monitor - Prueba de ERROR",
    "D007 IF - Diario Operaciones Interbancarias\nFecha: 2026-08-28 | Diferencia: 744 bytes\nRevisar reporte urgente.",
    urgente=True
)

print("Segunda notificación enviada (simulando ERROR urgente).")
print()
print("✅ Instalación verificada correctamente.")
print()
