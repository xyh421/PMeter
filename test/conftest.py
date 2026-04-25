"""
Shared fixtures for all PMeter test cases.
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pmeter.http import HttpClient
from pmeter.runner import Environment
from pmeter.stats import StatsCollector


# ---------------------------------------------------------------------------
# Mock HTTP server — covers all endpoints used across feature tests
# ---------------------------------------------------------------------------

class _MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        routes = {
            "/status/200":   lambda: self._send(200, b"OK"),
            "/status/404":   lambda: self._send(404, b"Not Found"),
            "/status/500":   lambda: self._send(500, b"Server Error"),
            "/json":         self._json,
            "/set-cookie":   lambda: self._send(200, b"ok", extra={"Set-Cookie": "session=abc123; Path=/"}),
            "/token":        self._token,
            "/slow":         self._slow,
            "/need-auth":    self._need_auth,
            "/inventory/42": self._inventory,
        }
        handler = routes.get(self.path, lambda: self._send(404, b"not found"))
        handler()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        routes = {
            "/login":      lambda: self._login(body),
            "/auth/token": lambda: self._send(200, json.dumps({"access_token": "tok-xyz-789"}).encode(), "application/json"),
        }
        handler = routes.get(self.path, lambda: self._send(200, body or b"posted"))
        handler()

    def _json(self):
        body = json.dumps({"slideshow": {"author": "Yours Truly", "items": [1, 2, 3]}}).encode()
        self._send(200, body, "application/json")

    def _token(self):
        body = json.dumps({"access_token": "tok-xyz-789", "expires_in": 3600}).encode()
        self._send(200, body, "application/json")

    def _slow(self):
        time.sleep(0.15)
        self._send(200, b"slow response")

    def _need_auth(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            self._send(200, json.dumps({"data": "secret"}).encode(), "application/json")
        else:
            self._send(401, b"Unauthorized")

    def _inventory(self):
        body = json.dumps({"id": 42, "stock": 10, "price": 299.9}).encode()
        self._send(200, body, "application/json")

    def _login(self, raw: bytes):
        try:
            data = json.loads(raw)
            if data.get("user") and data.get("pass"):
                self._send(200, json.dumps({"result": "ok"}).encode(), "application/json")
            else:
                self._send(400, b"missing credentials")
        except Exception:
            self._send(400, b"bad request")

    def _send(self, code: int, body: bytes, ctype: str = "text/plain", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@pytest.fixture(scope="session")
def mock_server():
    """Session-scoped local HTTP server — no internet required."""
    server = HTTPServer(("127.0.0.1", 0), _MockHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture
def env(mock_server):
    """Fresh Environment per test."""
    stats = StatsCollector()
    stop = threading.Event()
    return Environment(host=mock_server, stats=stats, stop_event=stop)


@pytest.fixture
def client(env):
    """Async HttpClient. Use arun(client.get(...)) in sync tests."""
    c = HttpClient(env, env.host)
    yield c
    asyncio.run(c.close())


def arun(coro):
    """Run an async coroutine from a sync test."""
    return asyncio.run(coro)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
