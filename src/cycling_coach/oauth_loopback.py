"""Servidor HTTP local de un solo uso para capturar el `code` de OAuth.

Strava redirige el navegador a http://localhost:<port>/callback?code=...
Este servidor atiende esa única petición, extrae el `code` y se apaga.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

_PAGE_OK = (
    "<html><body style='font-family:sans-serif'>"
    "<h2>Autorizacion recibida ✔</h2>"
    "<p>Ya puedes volver a la terminal.</p></body></html>"
)
_PAGE_ERR = (
    "<html><body style='font-family:sans-serif'>"
    "<h2>Error de autorizacion</h2><p>Revisa la terminal.</p></body></html>"
)


def wait_for_code(port: int, timeout_s: float = 300.0) -> str:
    """Bloquea hasta recibir el callback de OAuth y devuelve el `code`."""
    captured: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (nombre impuesto por la stdlib)
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            params = parse_qs(parsed.query)
            if "code" in params:
                captured["code"] = params["code"][0]
                body = _PAGE_OK
            else:
                captured["error"] = params.get("error", ["unknown"])[0]
                body = _PAGE_ERR
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            done.set()

        def log_message(self, *args: object) -> None:  # silencia el logging del server
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not done.wait(timeout=timeout_s):
            raise TimeoutError("No se recibió el callback de OAuth a tiempo.")
    finally:
        server.shutdown()
        server.server_close()

    if "error" in captured:
        raise RuntimeError(f"Strava devolvió un error de autorización: {captured['error']}")
    return captured["code"]
