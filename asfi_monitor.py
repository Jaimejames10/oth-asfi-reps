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

import argparse
import io
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import schedule
import reportes_db

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN  (editar según necesidad)
# ──────────────────────────────────────────────────────────────────────────────
CONFIG = {
    # URL base del sistema SCIP (sin barra final)
    "url_base": "https://appweb.asfi.gob.bo/SCIP",

    # Credenciales: se leen desde SQLite; estas variables/CLI son overrides temporales
    "usuario": os.environ.get("ASFI_USUARIO", ""),
    "password": os.environ.get("ASFI_PASSWORD", ""),

    # días_atras se conserva por compatibilidad con el CLI.
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

    # Archivo legado que se importa una vez a SQLite, si existe
    "archivo_no_enviados": "reportes_no_enviados.json",

    # Base SQLite con catálogo, obligaciones, historial y credenciales
    "archivo_base_datos": "asfi_monitor.db",
    "archivo_semilla": "reportes_seed.json",

    # Mostrar navegador (True para depuración, False para producción)
    "headless": True,

    # Tiempo máximo de espera para elementos (segundos)
    # En headless se necesita más tiempo
    "timeout_segundos": 45,

    # Logo/ícono para las notificaciones de Windows (.ico, relativo a este script)
    "icono_notificacion": "assets/asfi.ico",
}

BASE_DIR = Path(__file__).resolve().parent


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


# ──────────────────────────────────────────────────────────────────────────────
# NOTIFICACIONES
# ──────────────────────────────────────────────────────────────────────────────
def notificar(titulo: str, mensaje: str, urgente: bool = False) -> None:
    """
    Envía notificaciones nativas de Windows.
    Prueba los métodos secuencialmente y detiene el proceso cuando uno funciona.
    Esto evita mostrar la misma alerta varias veces en Windows.
    """

    # Sanitizar caracteres especiales para logging
    titulo_log = titulo.encode("ascii", "replace").decode("ascii")
    mensaje_log = mensaje[:100].encode("ascii", "replace").decode("ascii")
    log.info(f"[NOTIFICACION] {titulo_log}: {mensaje_log}")

    # Limitar longitud para evitar problemas con APIs
    titulo_limpio = titulo[:128]
    mensaje_limpio = mensaje[:512]
    
    # Escapar comillas simples para PowerShell
    titulo_ps = titulo_limpio.replace("'", "''")
    mensaje_ps = mensaje_limpio.replace("'", "''")

    # Métodos de notificación
    def metodo_messagebox():
        """PowerShell - MessageBox (popup visible, bloquea hasta click)"""
        try:
            import subprocess
            ps_script = f"""
            [void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms')
            [System.Windows.Forms.MessageBox]::Show('{mensaje_ps}', '{titulo_ps}', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning)
            """
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Normal", "-Command", ps_script],
                timeout=120,  # Timeout largo para permitir que usuario lea
                capture_output=True,
                text=True,
            )
            log.debug("✓ Notificación enviada via PowerShell MessageBox")
            return True
        except Exception as e:
            log.debug(f"PowerShell MessageBox: {type(e).__name__}")
            return False

    def metodo_plyer():
        """Plyer - Toast notification (cross-platform)"""
        try:
            from plyer import notification
            # Duración en segundos (aumentada para urgentes)
            duracion = 30 if urgente else 15
            notification.notify(
                title=titulo_limpio,
                message=mensaje_limpio[:256],
                app_name="ASFI/SCIP Monitor",
                app_icon=str(RUTA_ICONO) if RUTA_ICONO else None,
                timeout=duracion,
            )
            log.debug(f"✓ Notificación enviada via plyer ({duracion}s)")
            return True
        except Exception as e:
            log.debug(f"plyer: {type(e).__name__}")
            return False

    def metodo_ballontip():
        """PowerShell - notificación nativa integrada de Windows"""
        try:
            import subprocess
            # Duración en ms (aumentada para urgentes)
            duracion_ms = 15000 if urgente else 10000  # 15s o 10s
            espera_s = 16 if urgente else 11  # Esperar un poco más que la duración
            # Ícono personalizado (logo ASFI) si está disponible, si no el genérico
            if RUTA_ICONO:
                icono_ps = str(RUTA_ICONO).replace("'", "''")
                linea_icono = f"$n.Icon = New-Object System.Drawing.Icon('{icono_ps}')"
            else:
                linea_icono = "$n.Icon = [System.Drawing.SystemIcons]::Warning"
            ps_script = f"""
            Add-Type -AssemblyName System.Windows.Forms
            Add-Type -AssemblyName System.Drawing
            $n = New-Object System.Windows.Forms.NotifyIcon
            {linea_icono}
            $n.BalloonTipTitle = '{titulo_ps}'
            $n.BalloonTipText = '{mensaje_ps}'
            $n.Visible = $True
            $n.ShowBalloonTip({duracion_ms})
            Start-Sleep -Seconds {espera_s}
            $n.Visible = $False
            """
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                timeout=120,
                capture_output=True,
                text=True,
            )
            log.debug(f"✓ Notificación enviada via PowerShell BalloonTip ({duracion_ms}ms)")
            return True
        except Exception as e:
            log.debug(f"PowerShell BalloonTip: {type(e).__name__}")
            return False

    # Usar un solo método por alerta. Varios proveedores simultáneos generan
    # notificaciones duplicadas para el mismo evento.
    if urgente:
        log.info("[URGENTE] Enviando notificación por un método disponible...")
        if metodo_ballontip():
            return
        if metodo_plyer():
            return
        if metodo_messagebox():
            return
        log.warning(f"No se pudo mostrar notificación: {titulo}")
    else:
        # Preferir la notificación nativa integrada de Windows.
        if metodo_ballontip():
            return
        if metodo_plyer():
            return
        if metodo_messagebox():
            return
        log.warning(f"No se pudo mostrar notificación: {titulo}")


# ──────────────────────────────────────────────────────────────────────────────
# ESTADO PERSISTENTE (para no repetir notificaciones)
# ──────────────────────────────────────────────────────────────────────────────
def cargar_estado() -> dict:
    path = ruta_archivo("archivo_estado")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"alertas_enviadas": {}, "ultima_revision": None}


def guardar_estado(estado: dict) -> None:
    ruta_archivo("archivo_estado").write_text(
        json.dumps(estado, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def clave_reporte(reporte: dict) -> str:
    """Genera clave única para un reporte (para deduplicar notificaciones)."""
    return f"{reporte['fecha_corte']}|{reporte['grupo']}|{reporte['envio']}"


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


# ──────────────────────────────────────────────────────────────────────────────
# ANÁLISIS DEL RESULTADO - VERSIÓN NUEVA
# ──────────────────────────────────────────────────────────────────────────────
def _normalizar_texto(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _es_error_validacion(value: str) -> bool:
    return _normalizar_texto(value) in {"error", "detalle error"}


def analizar_reporte_nuevo(validacion: str, envio: str) -> tuple[str, str]:
    """
    Analiza el estado del reporte usando:
      - validacion: Contenido de columna "Validación Formato/Consistencia"
      - envio: Contenido de columna "Envío/Reproceso"
    
    Devuelve (estado, detalle) donde estado es:
      'ERROR'     → Hay error en validación
      'EXITOSO'   → Reporte aceptado (Envío X)
      'PENDIENTE' → En proceso de recepción
      'DESCONOCIDO' → No se puede determinar
    """
    validacion_normalizada = _normalizar_texto(validacion)
    envio_lower = envio.lower() if envio else ""

    # Verificar errores en validación (máxima prioridad)
    if _es_error_validacion(validacion):
        return "ERROR", f"Validación: {validacion.strip()[:100]}"

    # Verificar si está en proceso de recepción
    if "en proceso de recepción" in envio_lower:
        return "PENDIENTE", "En proceso de recepción"

    # Verificar si fue enviado correctamente (Envío 1, Envío 2, etc.)
    if envio_lower.startswith("envío"):
        return "EXITOSO", f"{envio.strip()}"

    # Si no hay validación y envío dice algo, puede ser exitoso
    if envio.strip() and not _es_error_validacion(validacion):
        return "EXITOSO", f"Envío: {envio.strip()[:100]}"

    return "DESCONOCIDO", f"Validación: {validacion.strip()[:50]} | Envío: {envio.strip()[:50]}"


def _leer_texto_celda(celda) -> str:
    """Lee texto visible o el valor de controles usados por DevExpress."""
    textarea = celda.locator("textarea")
    if textarea.count() > 0:
        return textarea.first.input_value().strip()
    return celda.inner_text().strip()


def _obtener_indices_columnas(page) -> tuple[Optional[int], Optional[int]]:
    """Obtiene los índices de validación y envío desde los encabezados de SCIP."""
    indice_validacion = None
    indice_envio = None
    try:
        cabeceras = page.locator("tr.dxgvHeader").first.locator("td")
        for index in range(cabeceras.count()):
            texto = " ".join(_leer_texto_celda(cabeceras.nth(index)).casefold().split())
            if "valid" in texto and ("formato" in texto or "consistencia" in texto):
                indice_validacion = index
            elif "env" in texto and "reproceso" in texto:
                indice_envio = index
    except Exception:
        pass
    return indice_validacion, indice_envio


def analizar_resultado(texto: str) -> tuple[str, str]:
    """
    [DEPRECATED] Mantener por compatibilidad.
    La nueva función es analizar_reporte_nuevo().
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
    Selecciona todo el texto con Ctrl+A y luego escribe la fecha nueva.
    Después presiona Tab para que el componente procese el valor y dispare
    el evento onchange interno de ASPx.
    """
    campo = page.locator(f"#{selector_id}")
    campo.wait_for(state="visible", timeout=timeout_ms)
    campo.scroll_into_view_if_needed()
    campo.click()                        # foco en el campo
    page.wait_for_timeout(200)
    campo.press("Control+a")             # seleccionar todo el texto existente
    page.wait_for_timeout(150)
    campo.press("Delete")                # borrar selección
    page.wait_for_timeout(150)
    campo.type(fecha_str, delay=60)      # escribir carácter a carácter (más robusto con DevExpress)
    page.wait_for_timeout(300)
    campo.press("Tab")                   # confirmar valor y disparar onchange de ASPx
    page.wait_for_timeout(700)           # dejar que DevExpress procese el cambio


def obtener_reportes(
    fecha_inicio: Optional[date] = None, fecha_fin: Optional[date] = None
) -> list[dict]:
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

    # Las llamadas directas al scraper también consumen las credenciales de DB.
    if not CONFIG["usuario"] or not CONFIG["password"]:
        try:
            usuario_db, password_db = cargar_credenciales_desde_db()
            if not CONFIG["usuario"]:
                CONFIG["usuario"] = usuario_db
            if not CONFIG["password"]:
                CONFIG["password"] = password_db
        except Exception as exc:
            log.warning(f"No se pudieron cargar credenciales desde SQLite: {exc}")

    timeout_ms = CONFIG["timeout_segundos"] * 1000
    reportes = []

    # SCIP debe consultarse siempre para un único período: ayer. No se usan
    # rangos históricos aquí, aunque existan obligaciones atrasadas.
    fecha_ayer = reportes_db.local_now().date() - timedelta(days=1)
    fecha_inicio = fecha_ayer
    fecha_fin = fecha_ayer
    fmt_fecha_inicio = reportes_db.format_asfi_date(fecha_inicio)
    fmt_fecha_fin = reportes_db.format_asfi_date(fecha_fin)
    log.info(f"Fechas a consultar: {fmt_fecha_inicio} a {fmt_fecha_fin}")

    with sync_playwright() as p:
        log.info("Iniciando navegador Chromium...")
        
        # Opciones de lanzamiento para evitar detección de automatización
        launch_args = [
            "--disable-blink-features=AutomationControlled",  # Oculta navigator.webdriver
            "--disable-dev-shm-usage",                        # Evita problemas de memoria en headless
            "--no-first-run",                                 # Omite primera ejecución
            "--no-default-browser-check",                     # Omite checks de navegador por defecto
        ]
        
        browser = p.chromium.launch(
            headless=CONFIG["headless"],
            args=launch_args,
        )
        
        # User-agent realista (Chrome en Windows 10)
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="es-BO",
            user_agent=user_agent,
            # Deshabilitar service workers para evitar caché que rompa la sesión
            service_workers="block",
            # Headers para parecer más realista
            extra_http_headers={
                "Accept-Language": "es-BO,es;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        page = context.new_page()
        
        # Inyectar script para ocultar que es navegador automatizado
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
        """)
        
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
            
            # En headless, el JavaScript tarda más en renderizar los controles
            # Agregar delay extra para que DevExpress cargue completamente
            page.wait_for_timeout(2000)

            # Campos confirmados del HTML real del login
            log.info("Ingresando credenciales...")
            # Aumentar timeout a 50 segundos solo para la primera espera del elemento login
            page.locator(".txtLogin").wait_for(state="visible", timeout=50_000)
            page.locator(".txtLogin").fill(CONFIG["usuario"])
            page.wait_for_timeout(300)  # Dar tiempo entre acciones
            page.locator(".txtPasswd").fill(CONFIG["password"])
            page.wait_for_timeout(300)

            # El botón ejecuta SubmitsEncry() (AES) y luego hace postback
            # Usar click() directo — Playwright ejecuta el onclick automáticamente
            log.info("Enviando formulario de login...")
            page.locator("#MainContent_DefaultContent_LoginButton").click()
            
            # En headless, el servidor puede tomar más tiempo
            # Esperar tanto a la respuesta del servidor como a la navegación
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.wait_for_timeout(1500)  # Delay extra para que la página se estabilice

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

            # ── PASO 4: Configurar fecha inicial ──────────────────────────────
            log.info(f"Configurando Fecha Corte Inicial: {fmt_fecha_inicio}")
            _ingresar_fecha(
                page,
                "MainContent_DefaultContent_aspfechainicial_I",
                fmt_fecha_inicio,
                timeout_ms,
            )

            # ── PASO 5: Configurar fecha final ─────────────────────────────────
            log.info(f"Configurando Fecha Corte Final: {fmt_fecha_fin}")
            _ingresar_fecha(
                page,
                "MainContent_DefaultContent_aspxFechaFinal_I",
                fmt_fecha_fin,
                timeout_ms,
            )

            # ── PASO 6: Click en Buscar ───────────────────────────────────────
            # El <input id="BtnBuscar_I"> está hidden (display:none).
            # El elemento clickeable real es el <div id="MainContent_DefaultContent_BtnBuscar">
            # que es el contenedor visible del botón DevExpress.
            log.info("Ejecutando búsqueda...")
            btn_buscar = page.locator("#MainContent_DefaultContent_BtnBuscar")
            btn_buscar.wait_for(state="visible", timeout=timeout_ms)
            btn_buscar.click()
            # Esperar a que el callback de DevExpress termine y la tabla se actualice
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.wait_for_timeout(2500)

            # ── PASO 7: Extraer filas de la tabla (con paginación) ────────
            log.info("Extrayendo datos de la tabla...")
            indice_validacion, indice_envio = _obtener_indices_columnas(page)
            if indice_validacion is None:
                indice_validacion = 7
            log.debug(
                f"Columnas detectadas: validación={indice_validacion}, envío={indice_envio or 'última'}"
            )
            
            pagina_num = 1
            while True:
                log.info(f"Procesando página {pagina_num} de la tabla...")
                
                filas = page.locator("tr.dxgvDataRow")
                
                # Esperar a que aparezca al menos una fila en esta página
                try:
                    filas.first.wait_for(state="attached", timeout=10_000)
                except PWTimeout:
                    if pagina_num == 1:
                        log.warning("La tabla no devolvió filas para la fecha consultada.")
                    break
                
                n_filas = filas.count()
                log.info(f"  Filas encontradas en página {pagina_num}: {n_filas}")
                
                # Procesar cada fila de la página actual
                for i in range(n_filas):
                    fila = filas.nth(i)
                    celdas = fila.locator("td.dxgv")
                    n_celdas = celdas.count()

                    try:
                        tipo_entidad  = celdas.nth(0).inner_text().strip()
                        fecha_corte   = celdas.nth(1).inner_text().strip()
                        fecha_llegada = celdas.nth(2).inner_text().strip()
                        sigla         = celdas.nth(3).inner_text().strip()
                        grupo         = celdas.nth(4).inner_text().strip()
                        email         = celdas.nth(5).inner_text().strip()

                        # El resultado está en un <textarea readonly> dentro de la celda 6
                        celda_resultado = celdas.nth(6)
                        resultado_texto = _leer_texto_celda(celda_resultado)

                        # Validación Formato/Consistencia, detectada por encabezado
                        # y con celda 7 como respaldo para versiones distintas de SCIP.
                        validacion_texto = ""
                        enlaces_detalle_error = fila.locator(
                            "a[id*='BtnDetalleError'], a[href*='BtnDetalleError']"
                        )
                        for enlace_index in range(enlaces_detalle_error.count()):
                            enlace_texto = _leer_texto_celda(enlaces_detalle_error.nth(enlace_index))
                            if _es_error_validacion(enlace_texto):
                                # Las filas normales también tienen este enlace,
                                # pero su texto interno está vacío.
                                validacion_texto = enlace_texto
                                break
                        if not validacion_texto and indice_validacion < n_celdas:
                            validacion_texto = _leer_texto_celda(celdas.nth(indice_validacion))

                        # "Envío/Reproceso" suele ser la última celda.
                        indice_envio_actual = (
                            indice_envio if indice_envio is not None and indice_envio < n_celdas else n_celdas - 1
                        )
                        envio = _leer_texto_celda(celdas.nth(indice_envio_actual))

                        # Nueva lógica: usar validación + envío en lugar de resultado
                        estado, detalle = analizar_reporte_nuevo(validacion_texto, envio)

                        reporte = {
                            "tipo_entidad"      : tipo_entidad,
                            "fecha_corte"       : fecha_corte,
                            "fecha_llegada"     : fecha_llegada,
                            "sigla"             : sigla,
                            "grupo"             : grupo,
                            "email"             : email,
                            "resultado_raw"     : resultado_texto,
                            "validacion"        : validacion_texto,
                            "estado"            : estado,
                            "detalle"           : detalle,
                            "envio"             : envio,
                            "timestamp_revision": datetime.now().isoformat(),
                        }
                        reportes.append(reporte)
                        log.debug(f"  [{estado}] {grupo} ({fecha_corte}) → {detalle}")

                    except Exception as exc:
                        log.warning(f"Error procesando fila {i} (página {pagina_num}): {exc}")
                        continue
                
                # Verificar si existe el botón "Siguiente" (paginación)
                # Selector: <a class="dxp-button dxp-bi" onclick="ASPx.GVPagerOnClick(...,'PBN');">
                # La imagen dentro tiene alt="Siguiente"
                try:
                    btn_siguiente = page.locator("a.dxp-bi[onclick*='PBN']")
                    if btn_siguiente.count() > 0:
                        # Verificar que el botón está habilitado (no deshabilitado)
                        clase_btn = btn_siguiente.get_attribute("class")
                        if "dxp-bi-disabled" not in clase_btn:
                            log.info(f"  Haciendo clic en 'Siguiente' (página {pagina_num} → {pagina_num + 1})...")
                            btn_siguiente.click()
                            # Esperar a que se cargue la siguiente página
                            page.wait_for_load_state("networkidle", timeout=timeout_ms)
                            page.wait_for_timeout(1000)  # Extra delay para DevExpress
                            pagina_num += 1
                            continue
                except Exception as e:
                    log.debug(f"Error verificando botón siguiente: {e}")
                
                # Si no hay botón siguiente habilitado, terminamos
                log.info(f"No hay más páginas. Total registros extraídos: {len(reportes)}")
                break


        except PWTimeout as exc:
            log.error(f"Timeout esperando un elemento de la página: {exc}")
            notificar(
                "⚠️ ASFI/SCIP Monitor - Timeout",
                f"No se cargó la página en {CONFIG['timeout_segundos']}s. "
                "Verificar conexión o disponibilidad del sistema ASFI.",
            )
        except RuntimeError as exc:
            log.error(str(exc))
            notificar("🔴 ASFI/SCIP Monitor - Error de login", str(exc)[:250])
        except Exception as exc:
            log.error(f"Error inesperado en scraping: {exc}", exc_info=True)
            notificar(
                "⚠️ ASFI/SCIP Monitor - Error inesperado",
                f"{type(exc).__name__}: {str(exc)[:200]}",
            )
        finally:
            browser.close()
            log.info("Navegador cerrado.")

    return reportes


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


def ejecutar_revision() -> None:
    """Revisa los reportes y envía notificaciones si hay problemas."""
    log.info("=" * 60)
    current = reportes_db.local_now()
    log.info(f"Iniciando revisión: {current.strftime('%Y-%m-%d %H:%M:%S')}")

    if CONFIG.get("_usar_credenciales_db", False):
        try:
            CONFIG["usuario"], CONFIG["password"] = cargar_credenciales_desde_db()
        except Exception as exc:
            log.error(f"No se pudieron actualizar las credenciales desde SQLite: {exc}")
            return

    estado = cargar_estado()
    estado["ultima_revision"] = reportes_db.now_iso(current)

    db_path = inicializar_base_datos()
    conn = reportes_db.connect(db_path)
    reportes_db.ensure_obligations(conn, current)
    fecha_inicio, fecha_fin = reportes_db.get_query_date_range(conn, current)
    run_id = reportes_db.start_scrape_run(conn, fecha_inicio, fecha_fin)

    try:
        reportes = obtener_reportes(fecha_inicio, fecha_fin)
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

    if not reportes:
        reportes_db.finish_scrape_run(conn, run_id, "VACIO", 0)
        conn.close()
        log.warning("No se obtuvieron reportes (tabla vacía o error de scraping)")
        guardar_estado(estado)
        return

    sin_enviar_diarios: list[dict] = []
    try:
        reportes_db.store_observations(conn, run_id, reportes)
        evaluacion = reportes_db.evaluate_obligations(
            conn, reportes, reportes_db.local_now()
        )
        # Diarios del período vigente que aún no se registran como enviados
        # (ABIERTO/PENDIENTE): se avisan en cada ciclo mientras esté dentro
        # del plazo; al vencer pasan a FALTANTE y usan la alerta urgente.
        sin_enviar_diarios = [
            fila for fila in reportes_db.list_current_obligations(conn)
            if fila["tipo_periodo"] == "diario"
            and fila["estado"] in ("ABIERTO", "PENDIENTE")
        ]
        reportes_db.finish_scrape_run(conn, run_id, "OK", len(reportes))
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

    for r in reportes:
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
        log.warning(f"⚠️ Reportes semanales FALTANTES: {reportes_faltantes_semanales}")
        clave_faltantes = f"faltantes_semanales|{fmt_fecha}"
        # SIEMPRE notificar reportes semanales faltantes - sin antispam
        # (así se envía alerta cada ejecución hasta que se envíen)
        estado["alertas_enviadas"][clave_faltantes] = {
            "estado": "FALTANTE",
            "notificado": datetime.now().isoformat(),
        }
        listado = "\n".join(f"  • {r}" for r in reportes_faltantes_semanales)
        notificar(
            f"⚠️ ASFI/SCIP Monitor - {len(reportes_faltantes_semanales)} reportes SEMANALES FALTANTES",
            f"Fecha: {fmt_fecha}\n\n{listado}",
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

    # Listado en consola del estado de los reportes
    total_faltantes = (
        len(reportes_faltantes_diarios)
        + len(reportes_faltantes_semanales)
        + len(reportes_faltantes_mensuales)
    )
    log.info(
        f"Estado de los reportes "
        f"({len(exitosos)} ✅ | {len(errores)} ❌ | "
        f"{total_faltantes} ⚠️ | {len(sin_enviar_diarios)} ⏳):"
    )
    for r in exitosos:
        log.info(f"  ✅ {r['grupo']} ({r['sigla']} | corte {r['fecha_corte']})")
    for nombre in reportes_faltantes_diarios:
        log.info(f"  ⚠️ {nombre} (FALTANTE)")
    for fila in sin_enviar_diarios:
        sufijo = f" (envío {fila['ocurrencia']})" if fila["ocurrencia"] > 1 else ""
        log.info(f"  ⏳ {fila['nombre']}{sufijo} (SIN ENVIAR - dentro de plazo)")
    for nombre in reportes_faltantes_semanales:
        log.info(f"  ⚠️ {nombre} (SEMANAL FALTANTE)")
    for nombre in reportes_faltantes_mensuales:
        log.info(f"  ⚠️ {nombre} (MENSUAL FALTANTE)")
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
        f"{len(sin_enviar_diarios)} sin enviar (dentro de plazo)"
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
        help="Contraseña ASFI (override temporal sobre DB/entorno)"
    )
    parser.add_argument(
        "--configurar", action="store_true",
        help="Abrir la interfaz gráfica de configuración y salir"
    )
    args = parser.parse_args()

    try:
        db_path = inicializar_base_datos()
    except Exception as exc:
        print(f"\n❌ No se pudo inicializar SQLite: {exc}")
        sys.exit(1)

    if args.configurar:
        from gestionar_reportes import ejecutar_gui
        ejecutar_gui(db_path)
        return

    # Precedencia: argumentos CLI > variables de entorno > credenciales SQLite.
    try:
        usuario_db, password_db = cargar_credenciales_desde_db(db_path)
    except Exception as exc:
        print(f"\n❌ No se pudieron leer las credenciales de SQLite: {exc}")
        sys.exit(1)

    if not CONFIG["usuario"]:
        CONFIG["usuario"] = usuario_db
    if not CONFIG["password"]:
        CONFIG["password"] = password_db

    # Aplicar argumentos
    CONFIG["intervalo_minutos"] = args.intervalo
    CONFIG["headless"] = not args.visible
    CONFIG["dias_atras"] = args.dias
    if args.usuario:
        CONFIG["usuario"] = args.usuario
    if args.password:
        CONFIG["password"] = args.password
    CONFIG["_usar_credenciales_db"] = not (
        bool(args.usuario)
        or bool(args.password)
        or bool(os.environ.get("ASFI_USUARIO"))
        or bool(os.environ.get("ASFI_PASSWORD"))
    )

    # Validar credenciales
    if not CONFIG["usuario"] or not CONFIG["password"]:
        print(
            "\n⚠️  No se configuraron las credenciales.\n"
            "   Opciones:\n"
            "   1. Ejecutar: python gestionar_reportes.py\n"
            "   2. Variables de entorno: ASFI_USUARIO y ASFI_PASSWORD\n"
            "   3. Argumentos temporales: --usuario XXXX --password YYYY\n"
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
        "ASFI/SCIP Monitor iniciado",
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
        notificar("ASFI/SCIP Monitor detenido", "El monitoreo fue detenido manualmente.")


if __name__ == "__main__":
    main()
