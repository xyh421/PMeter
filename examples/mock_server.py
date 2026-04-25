"""
本地 Mock 服务器 —— 用于验证 PMeter 压测脚本

启动：
    python examples/mock_server.py

默认监听 http://localhost:8080
"""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

PORT = 8080


class MockHandler(BaseHTTPRequestHandler):
    # ------------------------------------------------------------------ routing
    def do_GET(self):
        path = urlparse(self.path).path
        routes = {
            "/health":        self._health,
            "/json":          self._json,
            "/slow":          self._slow,
            "/need-auth":     self._need_auth,
            "/inventory/42":  self._inventory,
        }
        handler = routes.get(path, self._not_found)
        handler()

    def do_POST(self):
        path = urlparse(self.path).path
        routes = {
            "/login":       self._login,
            "/auth/token":  self._auth_token,
        }
        handler = routes.get(path, self._not_found)
        handler()

    # ----------------------------------------------------------------- handlers
    def _health(self):
        self._send(200, {"status": "ok"})

    def _json(self):
        self._send(200, {
            "slideshow": {
                "author": "Yours Truly",
                "title":  "Sample Slide Show",
                "slides": [{"title": "Wake up to WonderWidgets!"}],
            }
        })

    def _slow(self):
        time.sleep(0.8)   # 模拟慢接口，触发后置处理器告警
        self._send(200, {"message": "done"})

    def _need_auth(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            self._send(200, {"data": "secret content"})
        else:
            self._send(401, {"error": "unauthorized"})

    def _inventory(self):
        self._send(200, {"id": 42, "stock": 10, "price": 299.9})

    def _login(self):
        body = self._read_body()
        user = body.get("user", "")
        pwd  = body.get("pass", "")
        if user and pwd:
            self._send(200, {"result": "ok", "user": user})
        else:
            self._send(400, {"error": "missing credentials"})

    def _auth_token(self):
        body = self._read_body()
        if body.get("user") == "alice" and body.get("pass") == "s3cr3t":
            self._send(200, {"access_token": "tok-abc123", "expires_in": 3600})
        else:
            self._send(401, {"error": "invalid credentials"})

    def _not_found(self):
        self._send(404, {"error": "not found"})

    # ------------------------------------------------------------------ helpers
    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _send(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), MockHandler)
    print(f"Mock server running on http://127.0.0.1:{PORT}")
    print("Press Ctrl+C to stop.\n")
    print("Available endpoints:")
    print("  GET  /health        -> 200 {status: ok}")
    print("  GET  /json          -> slideshow JSON")
    print("  GET  /slow          -> 800ms delay")
    print("  GET  /need-auth     -> 需要 Bearer token")
    print("  GET  /inventory/42  -> {stock, price}")
    print("  POST /login         -> {user, pass}")
    print("  POST /auth/token    -> 返回 access_token\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
