"""Test-only stand-in for the subset of the Supabase Storage REST API the Store uses.
Write-once semantics match production (x-upsert: false → 400 'already exists')."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PREFIX = "/storage/v1"


class _Handler(BaseHTTPRequestHandler):
    objects: dict[str, bytes] = {}
    lock = threading.Lock()

    def _key(self, kind: str) -> str | None:
        p = self.path.split("?")[0]
        base = f"{PREFIX}/object/{kind}"
        if p.startswith(base):
            return p[len(base):].lstrip("/")
        return None

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if self.path.startswith(f"{PREFIX}/object/list/") or self.path.startswith(f"{PREFIX}/object/sign/"):
            self._send(200, b'{"signedURL": "/object/sign/x"}' if "sign" in self.path else b"[]")
            return
        key = self._key("")
        if key is None:
            self._send(404, b"{}")
            return
        with self.lock:
            if key in self.objects and self.headers.get("x-upsert", "false") == "false":
                self._send(400, b'{"error": "Duplicate", "message": "The resource already exists"}')
                return
            self.objects[key] = body
        self._send(200, b'{"Key": "ok"}')

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith(f"{PREFIX}/bucket/"):
            self._send(200, b'{"id": "bucket"}')
            return
        key = self._key("authenticated")
        with self.lock:
            data = self.objects.get(key or "")
        if data is None:
            self._send(400, b'{"error": "not_found"}')
        else:
            self._send(200, data, "application/octet-stream")

    def do_HEAD(self) -> None:  # noqa: N802
        key = self._key("authenticated")
        self.send_response(200 if key in self.objects else 400)
        self.end_headers()

    def do_DELETE(self) -> None:  # noqa: N802
        self._send(200, b"[]")

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, *a: object) -> None:
        return


def serve(port: int) -> ThreadingHTTPServer:
    _Handler.objects = {}
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    import sys
    import time

    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 54321)
    while True:
        time.sleep(3600)
