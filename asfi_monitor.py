"""
ASFI SCIP Monitor - Monitor de Control de Envíos
=================================================
Monitorea reportes enviados al sistema ASFI/SCIP y notifica
via Windows Toast si detecta errores o fallos en los envíos.

Dependencias:
    pip install playwright plyer schedule
    playwright install chromium

Uso:
    python asfi_monitor.py
    python asfi_monitor.py --intervalo 10   # revisar cada 10 minutos
    python asfi_monitor.py --una-vez        # ejecutar solo una vez
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import schedule

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN  (editar según necesidad)
# ──────────────────────────────────────────────────────────────────────────────
CONFIG = {
    # URL base del sistema SCIP (sin barra final)
    "url_base": "https://appweb.asfi.gob.bo/SCIP",

    # Credenciales (también se pueden pasar por variable de entorno)
    "usuario": os.environ.get("ASFI_USUARIO", "TU_USUARIO_AQUI"),
    "password": os.environ.get("ASFI_PASSWORD", "TU_PASSWORD_AQUI"),

    # días_atras ya no se usa — siempre se consulta "ayer"
    # Se conserva por compatibilidad con argumentos CLI pero no afecta la fecha
    "dias_atras": 1,

    # Intervalo de monitoreo en minutos (se puede sobreescribir con --intervalo)
    "intervalo_minutos": 15,

    # Palabras clave que indican ÉXITO en el resultado del envío
    "palabras_exito": [
        "proceso finalizado",
        "validado saldos",
        "archivo recibido",
        "capturados correctamente",
        "registro realizado",
        "enviado correctamente",
        "recibido correctamente",
    ],

    # Palabras clave que indican ERROR (tiene prioridad sobre éxito)
    "palabras_error": [
        "error",
        "fallo",
        "fallido",
        "rechazado",
        "no válido",
        "no valido",
        "inválido",
        "invalido",
        "diferencia:",        # p.ej. "Diferencia: 500" indica inconsistencia
        "no se pudo",
        "exception",
        "timeout",
    ],

    # Diferencia de tamaño tolerable (0 = cualquier diferencia es error)
    "diferencia_maxima": 0,

    # Archivo donde se guardan alertas ya notificadas (evita spam)
    "archivo_estado": "asfi_estado.json",

    # Mostrar navegador (True para depuración, False para producción)
    "headless": True,

    # Tiempo máximo de espera para elementos (segundos)
    "timeout_segundos": 30,
}

# ──────────────────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("asfi_monitor.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("asfi_monitor")


# ──────────────────────────────────────────────────────────────────────────────
# NOTIFICACIONES
# ──────────────────────────────────────────────────────────────────────────────
def notificar(titulo: str, mensaje: str, urgente: bool = False) -> None:
    """Envía notificación nativa de Windows. Fallback a consola si falla."""
    log.info(f"[NOTIFICACIÓN] {titulo}: {mensaje}")

    # Intentar con plyer (cross-platform, funciona bien en Windows)
    try:
        from plyer import notification
        notification.notify(
            title=titulo,
            message=mensaje[:256],          # plyer tiene límite de caracteres
            app_name="ASFI Monitor",
            timeout=15 if urgente else 8,   # segundos que permanece visible
        )
        return
    except Exception as e:
        log.debug(f"plyer falló: {e}")

    # Fallback: win10toast (solo Windows)
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        toaster.show_toast(titulo, mensaje[:255], duration=10, threaded=True)
        return
    except Exception as e:
        log.debug(f"win10toast falló: {e}")

    # Fallback final: PowerShell balloon tip (siempre disponible en Windows)
    try:
        import subprocess
        ps_script = f"""
        Add-Type -AssemblyName System.Windows.Forms
        $n = New-Object System.Windows.Forms.NotifyIcon
        $n.Icon = [System.Drawing.SystemIcons]::Warning
        $n.BalloonTipTitle = '{titulo[:63]}'
        $n.BalloonTipText = '{mensaje[:255]}'
        $n.Visible = $True
        $n.ShowBalloonTip(10000)
        Start-Sleep -Seconds 3
        $n.Visible = $False
        """
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception as e:
        log.debug(f"PowerShell notification falló: {e}")
        # Sin más opciones, el log ya tiene el mensaje


# ──────────────────────────────────────────────────────────────────────────────
# ESTADO PERSISTENTE (para no repetir notificaciones)
# ──────────────────────────────────────────────────────────────────────────────
def cargar_estado() -> dict:
    path = Path(CONFIG["archivo_estado"])
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"alertas_enviadas": {}, "ultima_revision": None}


def guardar_estado(estado: dict) -> None:
    Path(CONFIG["archivo_estado"]).write_text(
        json.dumps(estado, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def clave_reporte(reporte: dict) -> str:
    """Genera clave única para un reporte (para deduplicar notificaciones)."""
    return f"{reporte['fecha_corte']}|{reporte['grupo']}|{reporte['envio']}"


# ──────────────────────────────────────────────────────────────────────────────
# ANÁLISIS DEL RESULTADO
# ──────────────────────────────────────────────────────────────────────────────
def analizar_resultado(texto: str) -> tuple[str, str]:
    """
    Devuelve (estado, detalle) donde estado es:
      'ERROR'   → reporte fallido/rechazado
      'EXITOSO' → reporte correcto
      'PENDIENTE' → capturado pero en validación
      'DESCONOCIDO' → no se puede determinar
    """
    if not texto or not texto.strip():
        return "DESCONOCIDO", "Sin resultado registrado"

    texto_lower = texto.lower()

    # Verificar errores primero (mayor prioridad)
    for palabra in CONFIG["palabras_error"]:
        if palabra in texto_lower:
            # Caso especial: "Diferencia: 0" es OK
            if palabra == "diferencia:":
                match = re.search(r"diferencia:\s*(\d+)", texto_lower)
                if match:
                    diff = int(match.group(1))
                    if diff <= CONFIG["diferencia_maxima"]:
                        continue  # Es 0, no es error
                    else:
                        return "ERROR", f"Diferencia de tamaño detectada: {diff}"
            else:
                # Extraer la línea que contiene el error
                for linea in texto.split("\n"):
                    if palabra in linea.lower():
                        return "ERROR", linea.strip()[:120]
                return "ERROR", f"Palabra clave de error: '{palabra}'"

    # Verificar éxito
    for palabra in CONFIG["palabras_exito"]:
        if palabra in texto_lower:
            if "capturados correctamente" in texto_lower:
                return "PENDIENTE", "Archivos capturados, en proceso de validación en ASFI"
            return "EXITOSO", "Proceso completado correctamente"

    return "DESCONOCIDO", texto.strip()[:120]


# ──────────────────────────────────────────────────────────────────────────────
# SCRAPING
# ──────────────────────────────────────────────────────────────────────────────
def _ingresar_fecha(page, selector_id: str, fecha_str: str, timeout_ms: int) -> None:
    """
    Ingresa una fecha en un campo DevExpress DateEdit del sistema ASFI.
    Usa triple_click para seleccionar el texto existente y luego lo reemplaza.
    Después presiona Tab para que el componente procese el valor y dispare
    el evento onchange interno de ASPx.
    """
    campo = page.locator(f"#{selector_id}")
    campo.wait_for(state="visible", timeout=timeout_ms)
    campo.scroll_into_view_if_needed()
    campo.triple_click()
    page.wait_for_timeout(200)
    campo.fill(fecha_str)
    page.wait_for_timeout(200)
    campo.press("Tab")
    page.wait_for_timeout(600)   # dejar que DevExpress procese el cambio


def obtener_reportes() -> list[dict]:
    """
    Abre el navegador, inicia sesión y extrae los datos de la tabla
    de Control de Plazos. Devuelve lista de dicts con cada reporte.

    Flujo de navegación confirmado con el HTML real:
      1. Login  →  class="txtLogin" / class="txtPasswd" / #MainContent_DefaultContent_LoginButton
      2. Menú principal  →  <a href="mControlPlazos/Default.aspx">Control de Envios</a>
      3. Menú lateral  →  <a href="ControlPlazos.aspx" id="MainContent_MenuLateral_I0i0_T">Envios</a>
      4. Filtro fechas  →  #MainContent_DefaultContent_aspfechainicial_I  /  aspxFechaFinal_I
      5. Buscar  →  #MainContent_DefaultContent_BtnBuscar
      6. Tabla  →  tr.dxgvDataRow
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    timeout_ms = CONFIG["timeout_segundos"] * 1000
    reportes = []

    # La fecha que se busca es SIEMPRE el día anterior al de hoy,
    # que es cuando los reportes diarios ya deberían estar enviados.
    ayer = date.today() - timedelta(days=1)
    fmt_fecha = f"{ayer.day}/{ayer.month}/{ayer.year}"   # formato d/M/yyyy que usa el sistema
    log.info(f"Fecha a consultar: {fmt_fecha} (ayer)")

    with sync_playwright() as p:
        log.info("Iniciando navegador Chromium...")
        browser = p.chromium.launch(headless=CONFIG["headless"])
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="es-BO",
            # Deshabilitar service workers para evitar caché que rompa la sesión
            service_workers="block",
        )
        page = context.new_page()
        # Interceptar diálogos inesperados (alertas JS) y aceptarlos
        page.on("dialog", lambda d: d.accept())

        try:
            # ── PASO 1: Login ─────────────────────────────────────────────────
            url_login = (
                f"{CONFIG['url_base']}/Login.aspx"
                "?ReturnUrl=%2fSCIP%2fmControlPlazos%2fControlPlazos.aspx"
            )
            log.info(f"Abriendo login: {url_login}")
            page.goto(url_login, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=timeout_ms)

            # Campos confirmados del HTML real del login
            log.info("Ingresando credenciales...")
            page.locator(".txtLogin").wait_for(state="visible", timeout=timeout_ms)
            page.locator(".txtLogin").fill(CONFIG["usuario"])
            page.locator(".txtPasswd").fill(CONFIG["password"])

            # El botón ejecuta SubmitsEncry() (AES) y luego hace postback
            # Usar click() directo — Playwright ejecuta el onclick automáticamente
            log.info("Enviando formulario de login...")
            page.locator("#MainContent_DefaultContent_LoginButton").click()
            page.wait_for_load_state("networkidle", timeout=timeout_ms)

            # Verificar login exitoso: la URL ya no debe contener "Login.aspx"
            url_actual = page.url
            log.info(f"URL después del login: {url_actual}")
            if "login" in url_actual.lower():
                # Puede haber un mensaje de error visible en la página
                msg_error = page.locator("#MainContent_DefaultContent_lblResultado").inner_text()
                raise RuntimeError(
                    f"Login fallido. URL sigue siendo el login. "
                    f"Mensaje del sistema: '{msg_error.strip()}'. "
                    f"Verificar usuario/contraseña."
                )
            log.info("✅ Login exitoso")

            # ── PASO 2: Click en "Control de Envios" del menú principal ───────
            # Selector: <a class="level1 level1cabezera static" href="mControlPlazos/Default.aspx">
            log.info("Navegando a 'Control de Envios' en el menú principal...")
            enlace_control = page.locator("a.level1[href*='mControlPlazos/Default.aspx']")
            enlace_control.wait_for(state="visible", timeout=timeout_ms)
            enlace_control.click()
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.wait_for_timeout(1000)
            log.info(f"URL tras click menú principal: {page.url}")

            # ── PASO 3: Click en "Envios" del menú lateral ────────────────────
            # Selector: <a href="ControlPlazos.aspx" id="MainContent_MenuLateral_I0i0_T">
            log.info("Navegando a 'Envios' en el menú lateral...")
            enlace_envios = page.locator("#MainContent_MenuLateral_I0i0_T")
            enlace_envios.wait_for(state="visible", timeout=timeout_ms)
            enlace_envios.click()
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.wait_for_timeout(1500)   # DevExpress necesita tiempo extra para renderizar
            log.info(f"URL tras click menú lateral: {page.url}")

            # ── PASO 4: Configurar fecha inicial = ayer ───────────────────────
            log.info(f"Configurando Fecha Corte Inicial: {fmt_fecha}")
            _ingresar_fecha(
                page,
                "MainContent_DefaultContent_aspfechainicial_I",
                fmt_fecha,
                timeout_ms,
            )

            # ── PASO 5: Configurar fecha final = ayer (mismo día) ─────────────
            log.info(f"Configurando Fecha Corte Final: {fmt_fecha}")
            _ingresar_fecha(
                page,
                "MainContent_DefaultContent_aspxFechaFinal_I",
                fmt_fecha,
                timeout_ms,
            )

            # ── PASO 6: Click en Buscar ───────────────────────────────────────
            log.info("Ejecutando búsqueda...")
            btn_buscar = page.locator("#MainContent_DefaultContent_BtnBuscar")
            btn_buscar.wait_for(state="visible", timeout=timeout_ms)
            btn_buscar.click()
            # Esperar a que el callback de DevExpress termine y la tabla se actualice
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.wait_for_timeout(2500)

            # ── PASO 7: Extraer filas de la tabla ────────────────────────────
            log.info("Extrayendo datos de la tabla...")
            filas = page.locator("tr.dxgvDataRow")

            # Esperar a que aparezca al menos una fila (o confirmar que no hay)
            try:
                filas.first.wait_for(state="attached", timeout=10_000)
            except PWTimeout:
                log.warning("La tabla no devolvió filas para la fecha consultada.")
                return reportes

            n_filas = filas.count()
            log.info(f"Registros encontrados: {n_filas}")

            for i in range(n_filas):
                fila = filas.nth(i)
                celdas = fila.locator("td.dxgv")

                try:
                    tipo_entidad  = celdas.nth(0).inner_text().strip()
                    fecha_corte   = celdas.nth(1).inner_text().strip()
                    fecha_llegada = celdas.nth(2).inner_text().strip()
                    sigla         = celdas.nth(3).inner_text().strip()
                    grupo         = celdas.nth(4).inner_text().strip()
                    email         = celdas.nth(5).inner_text().strip()

                    # El resultado está en un <textarea readonly> dentro de la celda 6
                    celda_resultado = celdas.nth(6)
                    textarea = celda_resultado.locator("textarea")
                    resultado_texto = (
                        textarea.input_value()
                        if textarea.count() > 0
                        else celda_resultado.inner_text().strip()
                    )

                    # "Envío/Reproceso" es siempre la última celda
                    envio = celdas.last.inner_text().strip()

                    estado, detalle = analizar_resultado(resultado_texto)

                    reporte = {
                        "tipo_entidad"      : tipo_entidad,
                        "fecha_corte"       : fecha_corte,
                        "fecha_llegada"     : fecha_llegada,
                        "sigla"             : sigla,
                        "grupo"             : grupo,
                        "email"             : email,
                        "resultado_raw"     : resultado_texto,
                        "estado"            : estado,
                        "detalle"           : detalle,
                        "envio"             : envio,
                        "timestamp_revision": datetime.now().isoformat(),
                    }
                    reportes.append(reporte)
                    log.debug(f"  [{estado}] {grupo} ({fecha_corte}) → {detalle}")

                except Exception as exc:
                    log.warning(f"Error procesando fila {i}: {exc}")
                    continue

        except PWTimeout as exc:
            log.error(f"Timeout esperando un elemento de la página: {exc}")
            notificar(
                "⚠️ ASFI Monitor - Timeout",
                f"No se cargó la página en {CONFIG['timeout_segundos']}s. "
                "Verificar conexión o disponibilidad del sistema ASFI.",
            )
        except RuntimeError as exc:
            log.error(str(exc))
            notificar("🔴 ASFI Monitor - Error de login", str(exc)[:250])
        except Exception as exc:
            log.error(f"Error inesperado en scraping: {exc}", exc_info=True)
            notificar(
                "⚠️ ASFI Monitor - Error inesperado",
                f"{type(exc).__name__}: {str(exc)[:200]}",
            )
        finally:
            browser.close()
            log.info("Navegador cerrado.")

    return reportes


# ──────────────────────────────────────────────────────────────────────────────
# LÓGICA PRINCIPAL DE MONITOREO
# ──────────────────────────────────────────────────────────────────────────────
def ejecutar_revision() -> None:
    """Revisa los reportes y envía notificaciones si hay problemas."""
    log.info("=" * 60)
    log.info(f"Iniciando revisión: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    estado = cargar_estado()
    estado["ultima_revision"] = datetime.now().isoformat()

    try:
        reportes = obtener_reportes()
    except Exception as e:
        log.error(f"Fallo crítico obteniendo reportes: {e}", exc_info=True)
        notificar(
            "🔴 ASFI Monitor - Fallo crítico",
            f"Error obteniendo reportes: {str(e)[:200]}",
            urgente=True,
        )
        guardar_estado(estado)
        return

    if not reportes:
        log.warning("No se obtuvieron reportes (tabla vacía o error de scraping)")
        guardar_estado(estado)
        return

    errores = []
    pendientes = []
    exitosos = []

    for r in reportes:
        clave = clave_reporte(r)

        if r["estado"] == "ERROR":
            errores.append(r)
            # Solo notificar si no se notificó antes
            if clave not in estado["alertas_enviadas"]:
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
            if clave in estado["alertas_enviadas"]:
                prev = estado["alertas_enviadas"][clave]
                if prev.get("estado") == "ERROR":
                    del estado["alertas_enviadas"][clave]
                    notificar(
                        f"✅ Reporte ASFI resuelto — {r['grupo'][:40]}",
                        f"El reporte '{r['grupo'][:60]}' ahora figura como exitoso.",
                    )

    # Resumen en log
    log.info(
        f"Resumen: {len(exitosos)} exitosos | "
        f"{len(pendientes)} pendientes | "
        f"{len(errores)} errores"
    )

    # Notificación de resumen SOLO si hay errores múltiples
    if len(errores) > 1:
        grupos_error = "\n".join(f"• {r['grupo'][:50]}" for r in errores[:5])
        if len(errores) > 5:
            grupos_error += f"\n...y {len(errores) - 5} más"
        notificar(
            f"🔴 ASFI Monitor — {len(errores)} reportes con ERROR",
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


# ──────────────────────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor de envíos ASFI/SCIP con notificaciones Windows"
    )
    parser.add_argument(
        "--intervalo", type=int, default=CONFIG["intervalo_minutos"],
        help="Intervalo de revisión en minutos (por defecto: 15)"
    )
    parser.add_argument(
        "--una-vez", action="store_true",
        help="Ejecutar solo una revisión y salir"
    )
    parser.add_argument(
        "--visible", action="store_true",
        help="Mostrar el navegador (útil para depuración)"
    )
    parser.add_argument(
        "--dias", type=int, default=CONFIG["dias_atras"],
        help="Días hacia atrás a consultar (por defecto: 1)"
    )
    parser.add_argument(
        "--usuario", type=str,
        help="Usuario ASFI (sobreescribe config y variable de entorno)"
    )
    parser.add_argument(
        "--password", type=str,
        help="Contraseña ASFI (sobreescribe config y variable de entorno)"
    )
    args = parser.parse_args()

    # Aplicar argumentos
    CONFIG["intervalo_minutos"] = args.intervalo
    CONFIG["headless"] = not args.visible
    CONFIG["dias_atras"] = args.dias
    if args.usuario:
        CONFIG["usuario"] = args.usuario
    if args.password:
        CONFIG["password"] = args.password

    # Validar credenciales
    if CONFIG["usuario"] == "TU_USUARIO_AQUI" or not CONFIG["usuario"]:
        print(
            "\n⚠️  No se configuraron las credenciales.\n"
            "   Opciones:\n"
            "   1. Editar CONFIG en asfi_monitor.py\n"
            "   2. Variables de entorno: ASFI_USUARIO y ASFI_PASSWORD\n"
            "   3. Argumentos: --usuario XXXX --password YYYY\n"
        )
        sys.exit(1)

    log.info("=" * 60)
    log.info("ASFI SCIP Monitor iniciado")
    log.info(f"  Usuario  : {CONFIG['usuario']}")
    log.info(f"  Intervalo: {CONFIG['intervalo_minutos']} minutos")
    log.info(f"  Headless : {CONFIG['headless']}")
    log.info(f"  Días atrás: {CONFIG['dias_atras']}")
    log.info("=" * 60)

    # Verificar playwright
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("\n❌ Playwright no instalado. Ejecutar:\n   pip install playwright\n   playwright install chromium\n")
        sys.exit(1)

    if args.una_vez:
        ejecutar_revision()
        return

    # Modo continuo: ejecutar ahora y luego cada N minutos
    notificar(
        "ASFI Monitor iniciado",
        f"Monitoreando reportes cada {CONFIG['intervalo_minutos']} min.",
    )
    ejecutar_revision()  # Primera ejecución inmediata

    schedule.every(CONFIG["intervalo_minutos"]).minutes.do(ejecutar_revision)
    log.info(f"Scheduler activo. Revisando cada {CONFIG['intervalo_minutos']} minutos.")
    log.info("Presionar Ctrl+C para detener.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)  # Revisar el scheduler cada 30 segundos
    except KeyboardInterrupt:
        log.info("Monitor detenido por el usuario.")
        notificar("ASFI Monitor detenido", "El monitoreo fue detenido manualmente.")


if __name__ == "__main__":
    main()
