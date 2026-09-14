"""Cliente Playwright para el sitio ASFI/SCIP."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from asfi_monitor_app.config import CONFIG as DEFAULT_CONFIG
from asfi_monitor_app.domain.analysis import _es_error_validacion, analizar_reporte_nuevo
from asfi_monitor_app.integrations.notifications import notificar as default_notificar
from asfi_monitor_app.storage import api as reportes_db


Notify = Callable[[str, str, bool], None]


def _leer_texto_celda(celda) -> str:
    """Lee texto visible o el valor de controles usados por DevExpress."""
    textarea = celda.locator("textarea")
    if textarea.count() > 0:
        return textarea.first.input_value().strip()
    return celda.inner_text().strip()


def _obtener_indices_columnas(page) -> tuple[Optional[int], Optional[int]]:
    """Obtiene los indices de validacion y envio desde los encabezados."""
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


def _ingresar_fecha(page, selector_id: str, fecha_str: str, timeout_ms: int) -> None:
    """Escribe una fecha en un control DevExpress DateEdit."""
    campo = page.locator(f"#{selector_id}")
    campo.wait_for(state="visible", timeout=timeout_ms)
    campo.scroll_into_view_if_needed()
    campo.click()
    page.wait_for_timeout(200)
    campo.press("Control+a")
    page.wait_for_timeout(150)
    campo.press("Delete")
    page.wait_for_timeout(150)
    campo.type(fecha_str, delay=60)
    page.wait_for_timeout(300)
    campo.press("Tab")
    page.wait_for_timeout(700)


def _cargar_credenciales(config: dict) -> None:
    if config["usuario"] and config["password"]:
        return
    try:
        db_path = reportes_db.resolve_path(config["archivo_base_datos"])
        reportes_db.initialize_database(
            db_path,
            reportes_db.resolve_path(config["archivo_semilla"]),
            reportes_db.resolve_path(config["archivo_no_enviados"]),
        )
        conn = reportes_db.connect(db_path)
        try:
            usuario, password = reportes_db.get_credentials(conn)
        finally:
            conn.close()
        if not config["usuario"]:
            config["usuario"] = usuario
        if not config["password"]:
            config["password"] = password
    except Exception as exc:
        logging.getLogger("asfi_monitor").warning(
            "No se pudieron cargar credenciales desde SQLite: %s", exc
        )


def obtener_reportes(
    fecha_inicio: Optional[date] = None,
    fecha_fin: Optional[date] = None,
    *,
    config: Optional[dict] = None,
    logger: Optional[logging.Logger] = None,
    notify: Optional[Notify] = None,
    icon_path: Optional[Path] = None,
) -> list[dict]:
    """Inicia sesion y extrae las filas de Control de Plazos."""
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    config = config or DEFAULT_CONFIG
    log = logger or logging.getLogger("asfi_monitor")
    if notify is None:
        icon_path = icon_path or reportes_db.resolve_path(config["icono_notificacion"])

        def notify(title: str, message: str, urgent: bool = False) -> None:
            default_notificar(title, message, urgent, logger=log, icon_path=icon_path)

    _cargar_credenciales(config)
    timeout_ms = config["timeout_segundos"] * 1000
    reportes = []
    fecha_ayer = reportes_db.local_now().date() - timedelta(days=1)
    fecha_inicio = fecha_inicio or fecha_ayer
    fecha_fin = fecha_fin or fecha_inicio
    if fecha_fin < fecha_inicio:
        raise ValueError("La fecha final no puede ser anterior a la fecha inicial")
    fmt_fecha_inicio = reportes_db.format_asfi_date(fecha_inicio)
    fmt_fecha_fin = reportes_db.format_asfi_date(fecha_fin)
    log.info("Fechas a consultar: %s a %s", fmt_fecha_inicio, fmt_fecha_fin)

    with sync_playwright() as playwright:
        log.info("Iniciando navegador Chromium...")
        browser = playwright.chromium.launch(
            headless=config["headless"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1366, "height": 868},
            locale="es-BO",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            service_workers="block",
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
        page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """
        )
        page.on("dialog", lambda dialog: dialog.accept())

        try:
            url_login = (
                f"{config['url_base']}/Login.aspx"
                "?ReturnUrl=%2fSCIP%2fmControlPlazos%2fControlPlazos.aspx"
            )
            log.info("Abriendo login: %s", url_login)
            page.goto(url_login, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.wait_for_timeout(2000)

            log.info("Ingresando credenciales...")
            page.locator(".txtLogin").wait_for(state="visible", timeout=50_000)
            page.locator(".txtLogin").fill(config["usuario"])
            page.wait_for_timeout(300)
            page.locator(".txtPasswd").fill(config["password"])
            page.wait_for_timeout(300)
            page.locator("#MainContent_DefaultContent_LoginButton").click()
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.wait_for_timeout(1500)

            if "login" in page.url.lower():
                msg_error = page.locator(
                    "#MainContent_DefaultContent_lblResultado"
                ).inner_text()
                raise RuntimeError(
                    "Login fallido. URL sigue siendo el login. "
                    f"Mensaje del sistema: '{msg_error.strip()}'. "
                    "Verificar usuario/contraseña."
                )
            log.info("Login exitoso")

            enlace_control = page.locator("a.level1[href*='mControlPlazos/Default.aspx']")
            enlace_control.wait_for(state="visible", timeout=timeout_ms)
            enlace_control.click()
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.wait_for_timeout(1000)

            enlace_envios = page.locator("#MainContent_MenuLateral_I0i0_T")
            enlace_envios.wait_for(state="visible", timeout=timeout_ms)
            enlace_envios.click()
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.wait_for_timeout(1500)

            _ingresar_fecha(
                page,
                "MainContent_DefaultContent_aspfechainicial_I",
                fmt_fecha_inicio,
                timeout_ms,
            )
            _ingresar_fecha(
                page,
                "MainContent_DefaultContent_aspxFechaFinal_I",
                fmt_fecha_fin,
                timeout_ms,
            )

            btn_buscar = page.locator("#MainContent_DefaultContent_BtnBuscar")
            btn_buscar.wait_for(state="visible", timeout=timeout_ms)
            btn_buscar.click()
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            page.wait_for_timeout(2500)

            indice_validacion, indice_envio = _obtener_indices_columnas(page)
            if indice_validacion is None:
                indice_validacion = 7

            pagina_num = 1
            while True:
                log.info("Procesando pagina %s de la tabla...", pagina_num)
                filas = page.locator("tr.dxgvDataRow")
                try:
                    filas.first.wait_for(state="attached", timeout=10_000)
                except PWTimeout:
                    if pagina_num == 1:
                        log.warning("La tabla no devolvio filas para la fecha consultada.")
                    break

                n_filas = filas.count()
                log.info("Filas encontradas en pagina %s: %s", pagina_num, n_filas)
                for index in range(n_filas):
                    fila = filas.nth(index)
                    celdas = fila.locator("td.dxgv")
                    n_celdas = celdas.count()
                    try:
                        tipo_entidad = celdas.nth(0).inner_text().strip()
                        fecha_corte = celdas.nth(1).inner_text().strip()
                        fecha_llegada = celdas.nth(2).inner_text().strip()
                        sigla = celdas.nth(3).inner_text().strip()
                        grupo = celdas.nth(4).inner_text().strip()
                        email = celdas.nth(5).inner_text().strip()
                        resultado_texto = _leer_texto_celda(celdas.nth(6))

                        validacion_texto = ""
                        enlaces = fila.locator(
                            "a[id*='BtnDetalleError'], a[href*='BtnDetalleError']"
                        )
                        for enlace_index in range(enlaces.count()):
                            enlace_texto = _leer_texto_celda(enlaces.nth(enlace_index))
                            if _es_error_validacion(enlace_texto):
                                validacion_texto = enlace_texto
                                break
                        if not validacion_texto and indice_validacion < n_celdas:
                            validacion_texto = _leer_texto_celda(celdas.nth(indice_validacion))

                        indice_envio_actual = (
                            indice_envio
                            if indice_envio is not None and indice_envio < n_celdas
                            else n_celdas - 1
                        )
                        envio = _leer_texto_celda(celdas.nth(indice_envio_actual))
                        estado, detalle = analizar_reporte_nuevo(validacion_texto, envio)
                        reporte = {
                            "tipo_entidad": tipo_entidad,
                            "fecha_corte": fecha_corte,
                            "fecha_llegada": fecha_llegada,
                            "sigla": sigla,
                            "grupo": grupo,
                            "email": email,
                            "resultado_raw": resultado_texto,
                            "validacion": validacion_texto,
                            "estado": estado,
                            "detalle": detalle,
                            "envio": envio,
                            "timestamp_revision": datetime.now().isoformat(),
                        }
                        reportes.append(reporte)
                        log.debug("[%s] %s (%s) -> %s", estado, grupo, fecha_corte, detalle)
                    except Exception as exc:
                        log.warning(
                            "Error procesando fila %s (pagina %s): %s",
                            index,
                            pagina_num,
                            exc,
                        )

                try:
                    btn_siguiente = page.locator("a.dxp-bi[onclick*='PBN']")
                    if btn_siguiente.count() > 0:
                        clase_btn = btn_siguiente.get_attribute("class") or ""
                        if "dxp-bi-disabled" not in clase_btn:
                            btn_siguiente.click()
                            page.wait_for_load_state("networkidle", timeout=timeout_ms)
                            page.wait_for_timeout(1000)
                            pagina_num += 1
                            continue
                except Exception as exc:
                    log.debug("Error verificando boton siguiente: %s", exc)
                break
        except PWTimeout as exc:
            log.error("Timeout esperando un elemento de la pagina: %s", exc)
            notify(
                "⚠️ ASFI/SCIP Monitor - Timeout",
                f"No se cargó la página en {config['timeout_segundos']}s. "
                "Verificar conexión o disponibilidad del sistema ASFI.",
            )
        except RuntimeError as exc:
            log.error(str(exc))
            notify("🔴 ASFI/SCIP Monitor - Error de login", str(exc)[:250])
        except Exception as exc:
            log.error("Error inesperado en scraping: %s", exc, exc_info=True)
            notify(
                "⚠️ ASFI/SCIP Monitor - Error inesperado",
                f"{type(exc).__name__}: {str(exc)[:200]}",
            )
        finally:
            browser.close()
            log.info("Navegador cerrado.")
    return reportes


def obtener_reportes_por_rangos(
    rangos: list[tuple[date, date]],
    *,
    config: Optional[dict] = None,
    logger: Optional[logging.Logger] = None,
    notify: Optional[Notify] = None,
    icon_path: Optional[Path] = None,
) -> list[dict]:
    """Consulta rangos separados y elimina filas repetidas."""
    if not rangos:
        return []
    if len(rangos) == 1:
        return obtener_reportes(
            *rangos[0], config=config, logger=logger, notify=notify, icon_path=icon_path
        )

    log = logger or logging.getLogger("asfi_monitor")
    log.info("Consultas separadas a SCIP: %s", len(rangos))
    reportes = []
    vistos: set[tuple] = set()
    for fecha_inicio, fecha_fin in rangos:
        filas = obtener_reportes(
            fecha_inicio,
            fecha_fin,
            config=config,
            logger=logger,
            notify=notify,
            icon_path=icon_path,
        )
        for reporte in filas:
            clave = tuple(
                reporte.get(campo, "")
                for campo in (
                    "tipo_entidad",
                    "fecha_corte",
                    "fecha_llegada",
                    "sigla",
                    "grupo",
                    "email",
                    "envio",
                    "validacion",
                    "estado",
                )
            )
            if clave not in vistos:
                vistos.add(clave)
                reportes.append(reporte)
    log.info("Total registros extraidos en consultas separadas: %s", len(reportes))
    return reportes
