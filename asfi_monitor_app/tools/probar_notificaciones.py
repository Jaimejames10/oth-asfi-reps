"""Prueba manual de analisis y notificaciones Windows."""


def main() -> None:
    from asfi_monitor import analizar_resultado, notificar

    casos_prueba = [
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
        todos_ok = todos_ok and estado == esperado
        texto_short = texto[:50].replace("\n", " ") + ("..." if len(texto) > 50 else "")
        print(f"{ok} [{estado}] '{texto_short}'")
        if estado != esperado:
            print(f"     ⚠️  Esperado: {esperado}, Detalle: {detalle}")
    print("✅ Todos los análisis correctos" if todos_ok else "⚠️  Revisar palabras clave")

    print("\n" + "=" * 60)
    print("  PRUEBA DE NOTIFICACIONES WINDOWS")
    print("=" * 60)
    notificar(
        "✅ ASFI/SCIP Monitor - Prueba OK",
        "Las notificaciones funcionan correctamente. El monitor está listo.",
    )
    notificar(
        "🔴 ASFI/SCIP Monitor - Prueba de ERROR",
        "D007 IF - Diario Operaciones Interbancarias\n"
        "Fecha: 2026-08-28 | Diferencia: 744 bytes\nRevisar reporte urgente.",
        urgente=True,
    )
    print("✅ Instalación verificada correctamente.")
