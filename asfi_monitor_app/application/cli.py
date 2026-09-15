"""Interfaz de linea de comandos y scheduler del monitor."""

from __future__ import annotations

import argparse
import os
import sys
import time

import schedule

from asfi_monitor_app.application import monitor_service as service


# LOGO_ASCII = r"""
#              or  uuuuuuuuuuuuuu q                                                                                                                                
#           q  qqqqqqqqqqqqqqqqqq q                                                                                                                                
#         q qqqq           qqqqqq q                                                                                                                                
#        q qqq               qqqq q                                                                                                                                
#       q qqs      qqqqq       qq q                                                                                                                                
#      rq qq     qqqqqqqqqqqqqqqq q    qqqqqqqo    qqqqqqqqqqqqqqqqqqq      oqqqqqqqqqq qq qqqqq    oqqqqqqqqqq  qqqqqqqqqqq      oqqqqqqqqqq                      
#      q qq      qqqqqqqqqqqqqqqq q  qqqqoooqqqqq  qqqqooooqqqqqoooqqqq   qqqqooooqqqqq qqqqqqqq  qqqqqoooqqqqq  qqqqqoooqqqqq  qqqqqoooqqqqq                      
#      q qqq     qqqqqqqqqqqqqqqq q qqqq      qqqq qqq     qqqq     qqqq qqqq      qqqq qqqq     qqqq       qqq  qqq       qqqxqqqq       qqq                      
#      rq qq      qqqqqqqqqqqqqqq q qqq       qqqq qqq     qqqq     qqqq qqq       qqqq qqqq     qqqq       qqq  qqq       qqqqqqqq       qqq                      
#       q uqq        qqqq     qq q  qqqq      qqq  qqq     qqqq     qqqq qqqq      qqqq qqqq     qqqq      qqqq  qqqq      qqq  qqq      qqqq   qrrq   qq          
#        rq qqq              qq q    qqqqqqqqqqq   qqq     qqqq     qqqq  qqqqqqqqqqqqq qqqq      qqqqqqqqqqqqq  qqqqqqqqqqqqx  qqqqqqqqqqqqq   qqqq   qq          
#          q qqqqr        qqqq r       qqqqqqq     qqq      qqq      qqq    qqqqqq  qqq qqqq        qqqqqqq qqq  qqq qqqqqqt      qqqqqqq qqq   q  q q qqqq q      
#            q  qqqqqqqqqqq  q                                                                                   qqq                                               
#               rq  qq  uq                                                                                       qqq                                               
# """.strip("\n")
LOGO_ASCII = """\n\n\n
             or  uuuuuuuuuuuuuu q                                                                                                                                
          q  qqqqqqqqqqqqqqqqqq q                                                                                                                                
        q qqqq           qqqqqq q                                                                                                                                
       q qqq               qqqq q                                                                                                                                
      q qqs      qqqqq       qq q                                                                                                                                
     rq qq     qqqqqqqqqqqqqqqq q    qqqqqqqo    qqqqqqqqqqqqqqqqqqq      oqqqqqqqqqq qq qqqqq    oqqqqqqqqqq  qqqqqqqqqqq      oqqqqqqqqqq                      
     q qq      qqqqqqqqqqqqqqqq q  qqqqoooqqqqq  qqqqooooqqqqqoooqqqq   qqqqooooqqqqq qqqqqqqq  qqqqqoooqqqqq  qqqqqoooqqqqq  qqqqqoooqqqqq                      
     q qqq     qqqqqqqqqqqqqqqq q qqqq      qqqq qqq     qqqq     qqqq qqqq      qqqq qqqq     qqqq       qqq  qqq       qqqxqqqq       qqq                      
     rq qq      qqqqqqqqqqqqqqq q qqq       qqqq qqq     qqqq     qqqq qqq       qqqq qqqq     qqqq       qqq  qqq       qqqqqqqq       qqq                      
      q uqq        qqqq     qq q  qqqq      qqq  qqq     qqqq     qqqq qqqq      qqqq qqqq     qqqq      qqqq  qqqq      qqq  qqq      qqqq   qrrq   qq          
       rq qqq              qq q    qqqqqqqqqqq   qqq     qqqq     qqqq  qqqqqqqqqqqqq qqqq      qqqqqqqqqqqqq  qqqqqqqqqqqqx  qqqqqqqqqqqqq   qqqq   qq          
         q qqqqr        qqqq r       qqqqqqq     qqq      qqq      qqq    qqqqqq  qqq qqqq        qqqqqqq qqq  qqq qqqqqqt      qqqqqqq qqq   q  q q qqqq q      
           q  qqqqqqqqqqq  q                                                                                   qqq                                               
              rq  qq  uq                                                                                       qqq                                               
\n\n"""


def imprimir_encabezado(config: dict, args: argparse.Namespace) -> None:
    """Muestra la información esencial sin exponer credenciales."""
    print("=" * 78)
    print("ASFI / SCIP MONITOR")
    print(
        f"Usuario: {config['usuario'] or '-'} | "
        f"Intervalo: {config['intervalo_minutos']} minutos"
    )
    print(
        f"Modo: {'visible' if args.visible else 'headless'} | "
        f"Consulta: {args.dias} día(s) hacia atrás | "
        f"Salida: {'detallada' if args.verbose else 'resumida'}"
    )
    print("=" * 78)


def main() -> None:
    print(LOGO_ASCII)
    print()
    config = service.CONFIG
    parser = argparse.ArgumentParser(
        description="Monitor de envíos ASFI/SCIP con notificaciones Windows"
    )
    parser.add_argument(
        "--intervalo", type=int, default=None,
        help="Intervalo de revisión en minutos (por defecto: 15)",
    )
    parser.add_argument(
        "--una-vez", action="store_true",
        help="Ejecutar solo una revisión y salir",
    )
    parser.add_argument(
        "--visible", action="store_true",
        help="Mostrar el navegador (útil para depuración)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Mostrar el detalle completo de la revisión en consola",
    )
    parser.add_argument(
        "--dias", type=int, default=config["dias_atras"],
        help="Días hacia atrás a consultar (por defecto: 1)",
    )
    parser.add_argument(
        "--usuario", type=str,
        help="Usuario ASFI (sobreescribe config y variable de entorno)",
    )
    parser.add_argument(
        "--password", type=str,
        help="Contraseña ASFI (override temporal sobre DB/entorno)",
    )
    parser.add_argument(
        "--configurar", action="store_true",
        help="Abrir la interfaz gráfica de configuración y salir",
    )
    args = parser.parse_args()
    service.configurar_salida_consola(args.verbose)
    config["_verbose"] = args.verbose

    try:
        db_path = service.inicializar_base_datos()
        service.cargar_configuracion_desde_db(db_path)
    except Exception as exc:
        print(f"\n❌ No se pudo inicializar SQLite: {exc}")
        sys.exit(1)

    if args.configurar:
        from asfi_monitor_app.ui.dashboard import ejecutar_gui

        ejecutar_gui(db_path)
        return

    try:
        usuario_db, password_db = service.cargar_credenciales_desde_db(db_path)
    except Exception as exc:
        print(f"\n❌ No se pudieron leer las credenciales de SQLite: {exc}")
        sys.exit(1)

    if not config["usuario"]:
        config["usuario"] = usuario_db
    if not config["password"]:
        config["password"] = password_db
    if args.intervalo is not None:
        if args.intervalo <= 0:
            parser.error("--intervalo debe ser mayor que cero")
        config["intervalo_minutos"] = args.intervalo
    config["_intervalo_fijado_cli"] = args.intervalo is not None
    config["headless"] = not args.visible
    config["dias_atras"] = args.dias
    if args.usuario:
        config["usuario"] = args.usuario
    if args.password:
        config["password"] = args.password
    config["_usar_credenciales_db"] = not (
        bool(args.usuario)
        or bool(args.password)
        or bool(os.environ.get("ASFI_USUARIO"))
        or bool(os.environ.get("ASFI_PASSWORD"))
    )

    if not config["usuario"] or not config["password"]:
        print(
            "\n⚠️  No se configuraron las credenciales.\n"
            "   Opciones:\n"
            "   1. Ejecutar: python gestionar_reportes.py\n"
            "   2. Variables de entorno: ASFI_USUARIO y ASFI_PASSWORD\n"
            "   3. Argumentos temporales: --usuario XXXX --password YYYY\n"
        )
        sys.exit(1)

    imprimir_encabezado(config, args)
    log = service.log
    log.info("=" * 60)
    log.info("ASFI SCIP Monitor iniciado")
    log.info("  Usuario  : %s", config["usuario"])
    log.info("  Intervalo: %s minutos", config["intervalo_minutos"])
    log.info("  Headless : %s", config["headless"])
    log.info("  Días atrás: %s", config["dias_atras"])
    log.info("  Salida detallada: %s", args.verbose)
    log.info("=" * 60)

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print(
            "\n❌ Playwright no instalado. Ejecutar:\n"
            "   pip install playwright\n   playwright install chromium\n"
        )
        sys.exit(1)

    if args.una_vez:
        service.ejecutar_revision()
        return

    service.notificar(
        "ASFI/SCIP Monitor iniciado",
        f"Monitoreando reportes cada {config['intervalo_minutos']} min.",
    )
    service.ejecutar_revision()
    schedule.every(config["intervalo_minutos"]).minutes.do(service.ejecutar_revision)
    print(
        f"\nMonitoreo activo cada {config['intervalo_minutos']} minutos. "
        "Presione Ctrl+C para detener."
    )
    log.info("Scheduler activo. Revisando cada %s minutos.", config["intervalo_minutos"])
    log.info("Presionar Ctrl+C para detener.")
    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("Monitor detenido por el usuario.")
        service.notificar("ASFI/SCIP Monitor detenido", "El monitoreo fue detenido manualmente.")
