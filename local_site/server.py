"""
Servidor local que simula el sitio ASFI/SCIP para pruebas del monitor.
=====================================================================
Sirve los HTML de local_site/ en las rutas que asfi_monitor.py espera,
maneja el POST del login (acepta cualquier usuario/contraseña no vacíos)
con cookie de sesión, y simula la paginación de la tabla de envíos.

No requiere dependencias externas (solo librería estándar).

Uso:
    python local_site/server.py            # http://127.0.0.1:5500/
    python local_site/server.py --puerto 8080
"""

from __future__ import annotations

import argparse
import re
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent
SESSION_COOKIE = "scip_session"
SESSION_VALUE = "local-test"

# Ruta (normalizada y en minúsculas) -> archivo HTML que la implementa
ROUTES = {
    "/login.aspx": "index.html",
    "/scip/default.aspx": "asfi-scip.html",
    "/scip/mcontrolplazos/default.aspx": "control-envio.html",
    "/scip/mcontrolplazos/controlplazos.aspx": "envio.html",
}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}

MSG_LOGIN_INVALIDO = "Usuario o contrase&ntilde;a no v&aacute;lidos."


def normalizar_ruta(path: str) -> str:
    """Colapsa barras duplicadas (/foo//bar -> /foo/bar) y decodifica %XX."""
    path = unquote(urlparse(path).path)
    path = re.sub(r"/+", "/", path)
    if not path.startswith("/"):
        path = "/" + path
    return path


def leer_html(nombre: str) -> str:
    return (BASE_DIR / nombre).read_text(encoding="utf-8")


class HandlerSciPLocal(BaseHTTPRequestHandler):
    server_version = "SCIPMock/1.0"

    # ── utilidades de respuesta ──────────────────────────────────────────
    def _enviar_html(self, html: str, status: int = HTTPStatus.OK) -> None:
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirigir(self, location: str, set_cookie: bool = False) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        if set_cookie:
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}={SESSION_VALUE}; Path=/; HttpOnly",
            )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _enviar_archivo(self, nombre: str) -> None:
        archivo = (BASE_DIR / nombre).resolve()
        # Evitar path traversal fuera de local_site/
        if BASE_DIR.resolve() not in archivo.parents:
            self._enviar_html("<h1>403 Forbidden</h1>", HTTPStatus.FORBIDDEN)
            return
        if not archivo.is_file():
            self._enviar_html("<h1>404 Not Found</h1>", HTTPStatus.NOT_FOUND)
            return
        data = archivo.read_bytes()
        ctype = CONTENT_TYPES.get(archivo.suffix.lower(), "application/octet-stream")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _tiene_sesion(self) -> bool:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(SESSION_COOKIE)
        return morsel is not None and morsel.value == SESSION_VALUE

    # ── GET ──────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 (API de http.server)
        ruta = normalizar_ruta(self.path)
        query = parse_qs(urlparse(self.path).query)
        clave = ruta.lower().rstrip("/") or "/"

        # Recursos binarios de DevExpress (imágenes del grid): devolver PNG 1x1
        if "dxr.axd" in clave:
            import base64
            png_1x1 = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
            )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png_1x1)))
            self.end_headers()
            self.wfile.write(png_1x1)
            return

        # Login (y raíz del sitio)
        if clave in ("/", "/login.aspx"):
            html = leer_html(ROUTES["/login.aspx"])
            if query.get("err", ["0"])[0] == "1":
                html = html.replace("<!--MSG-->", MSG_LOGIN_INVALIDO)
            self._enviar_html(html)
            return

        # Páginas internas: requieren sesión
        if clave in ROUTES:
            if not self._tiene_sesion():
                self._redirigir("/Login.aspx?err=1")
                return
            html = leer_html(ROUTES[clave])
            self._enviar_html(html)
            return

        # Cualquier otro recurso: servir como archivo estático si existe
        relativo = ruta.lstrip("/")
        if (BASE_DIR / relativo).is_file():
            self._enviar_archivo(relativo)
            return

        self._enviar_html("<h1>404 Not Found</h1>", HTTPStatus.NOT_FOUND)

    # ── POST (login) ─────────────────────────────────────────────────────
    def do_POST(self) -> None:  # noqa: N802
        ruta = normalizar_ruta(self.path)
        if ruta.lower().rstrip("/") != "/login.aspx":
            self._enviar_html("<h1>404 Not Found</h1>", HTTPStatus.NOT_FOUND)
            return

        longitud = int(self.headers.get("Content-Length", "0") or 0)
        cuerpo = self.rfile.read(longitud).decode("utf-8", errors="replace")
        campos = parse_qs(cuerpo, keep_blank_values=True)

        usuario = campos.get(
            "ctl00$ctl00$MainContent$DefaultContent$txtUsuario", [""]
        )[0].strip()
        password = campos.get(
            "ctl00$ctl00$MainContent$DefaultContent$txtPassword", [""]
        )[0].strip()

        # Mock: acepta cualquier credencial no vacía.
        if usuario and password:
            self._redirigir("/SCIP/Default.aspx", set_cookie=True)
        else:
            self._redirigir("/Login.aspx?err=1")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        print(f"[{self.log_date_time_string()}] {self.address_string()} {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock local del sitio ASFI/SCIP")
    parser.add_argument(
        "--puerto", type=int, default=5500,
        help="Puerto de escucha (por defecto: 5500, coincide con CONFIG url_base)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host de escucha")
    args = parser.parse_args()

    servidor = ThreadingHTTPServer((args.host, args.puerto), HandlerSciPLocal)
    print(f"Mock ASFI/SCIP escuchando en http://{args.host}:{args.puerto}/")
    print("Login: cualquier usuario y contrasena no vacios.")
    print("Ctrl+C para detener.")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
        servidor.server_close()


if __name__ == "__main__":
    main()
