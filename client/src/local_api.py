"""ローカル HTTP API (ブラウザロビーから asobby クライアントへ問い合わせ)。"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urlparse

from host_probe import probe_rtt_ms
from services import __version__

DEFAULT_PORT = 49152
LOCAL_API_PORT = int(os.environ.get("ASOBBY_LOCAL_API_PORT", str(DEFAULT_PORT)))

ALLOWED_ORIGINS = {
    "https://asobby.com",
    "https://www.asobby.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
}

_server: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="asobby-ping")


def _cors_origin(origin: str | None) -> str:
    if origin and origin in ALLOWED_ORIGINS:
        return origin
    return "https://asobby.com"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _write_cors_headers(self, origin: str) -> None:
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        # https://asobby.com → 127.0.0.1 呼び出し (Chrome Private Network Access)
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Vary", "Origin")

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        origin = _cors_origin(self.headers.get("Origin"))
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._write_cors_headers(origin)
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:
        origin = _cors_origin(self.headers.get("Origin"))
        self.send_response(204)
        self._write_cors_headers(origin)
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "version": __version__,
                    "port": LOCAL_API_PORT,
                },
            )
            return
        self._send_json(404, {"ok": False, "detail": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/lobby/ping":
            self._send_json(404, {"ok": False, "detail": "not found"})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"ok": False, "detail": "invalid json"})
            return

        targets = body.get("targets")
        if not isinstance(targets, list):
            self._send_json(422, {"ok": False, "detail": "targets required"})
            return
        if len(targets) > 32:
            self._send_json(422, {"ok": False, "detail": "too many targets"})
            return

        jobs: list[tuple[str, str, int, bool]] = []
        for item in targets:
            if not isinstance(item, dict):
                continue
            post_id = str(item.get("id") or "")
            host = str(item.get("host") or "")
            try:
                port = int(item.get("port"))
            except (TypeError, ValueError):
                continue
            if not post_id or not host:
                continue
            autopunch = bool(item.get("autopunch"))
            jobs.append((post_id, host, port, autopunch))

        results: list[dict[str, Any]] = []
        futures = {
            _executor.submit(
                probe_rtt_ms,
                host,
                port,
                autopunch=autopunch,
            ): post_id
            for post_id, host, port, autopunch in jobs
        }
        for fut in as_completed(futures):
            post_id = futures[fut]
            try:
                rtt_ms = fut.result()
            except Exception:
                rtt_ms = None
            results.append(
                {
                    "id": post_id,
                    "rtt_ms": rtt_ms,
                    "ok": rtt_ms is not None,
                }
            )

        self._send_json(200, {"ok": True, "results": results})


def start_local_api_server(port: int | None = None) -> int:
    """127.0.0.1 でローカル API を起動する (既に起動済みなら port を返す)。"""
    global _server, _server_thread
    if _server is not None:
        return LOCAL_API_PORT

    bind_port = port if port is not None else LOCAL_API_PORT

    def _run() -> None:
        global _server
        httpd = ThreadingHTTPServer(("127.0.0.1", bind_port), _Handler)
        _server = httpd
        httpd.serve_forever(poll_interval=0.5)

    thread = threading.Thread(
        target=_run,
        name="asobby-local-api",
        daemon=True,
    )
    thread.start()
    _server_thread = thread
    return bind_port
