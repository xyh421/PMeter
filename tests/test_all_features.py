"""
Comprehensive test suite for all PMeter features.

Runs without network access using a local mock HTTP server.
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pmeter.csv_data import CsvDataSet
from pmeter.dsl import task
from pmeter.http import HttpAssertionError, HttpClient, HttpResponse
from pmeter.processors import post_processor, pre_processor
from pmeter.report import generate_html_report
from pmeter.runner import Environment, HttpUser, RunResult, run
from pmeter.stats import CheckEntry, StatsCollector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def arun(coro):
    """Run a coroutine in a fresh event loop (for sync tests)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Shared mock HTTP server
# ---------------------------------------------------------------------------

class _MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/status/200":
            self._send(200, b"OK")
        elif self.path == "/status/404":
            self._send(404, b"Not Found")
        elif self.path == "/json":
            body = json.dumps({
                "slideshow": {"author": "Yours Truly", "items": [1, 2, 3]}
            }).encode()
            self._send(200, body, "application/json")
        elif self.path == "/set-cookie":
            self._send(200, b"ok", extra={"Set-Cookie": "session=abc123"})
        elif self.path == "/token":
            body = json.dumps({"access_token": "tok-xyz-789"}).encode()
            self._send(200, body, "application/json")
        elif self.path == "/slow":
            time.sleep(0.1)
            self._send(200, b"slow")
        else:
            self._send(404, b"not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self._send(200, body or b"posted")

    def _send(self, code, body, ctype="text/plain", extra=None):
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
    server = HTTPServer(("127.0.0.1", 0), _MockHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture
def env(mock_server):
    stats = StatsCollector()
    stop = threading.Event()
    return Environment(host=mock_server, stats=stats, stop_event=stop)


@pytest.fixture
def client(env):
    """Return an HttpClient whose async methods can be called via arun()."""
    c = HttpClient(env, env.host)
    yield c
    arun(c.close())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# 1. CSV 参数化
# ---------------------------------------------------------------------------

class TestCsvDataSet:
    def test_reads_rows(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("name,age\nalice,30\nbob,25\n")
        ds = CsvDataSet(f)
        assert len(ds) == 2
        rows = list(ds)
        assert rows[0] == {"name": "alice", "age": "30"}
        assert rows[1] == {"name": "bob", "age": "25"}

    def test_cycles(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("x\n1\n2\n")
        ds = CsvDataSet(f)
        assert ds.next_row() == {"x": "1"}
        assert ds.next_row() == {"x": "2"}
        assert ds.next_row() == {"x": "1"}

    def test_thread_safe(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("id\n" + "\n".join(str(i) for i in range(10)))
        ds = CsvDataSet(f)
        seen = []
        lock = threading.Lock()

        def worker():
            for _ in range(20):
                row = ds.next_row()
                with lock:
                    seen.append(row["id"])

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(seen) == 100

    def test_custom_delimiter(self, tmp_path):
        f = tmp_path / "data.tsv"
        f.write_text("a\tb\n1\t2\n")
        ds = CsvDataSet(f, delimiter="\t")
        assert ds.next_row() == {"a": "1", "b": "2"}

    def test_empty_csv_raises(self, tmp_path):
        f = tmp_path / "empty.csv"
        f.write_text("name\n")
        with pytest.raises(ValueError, match="no data rows"):
            CsvDataSet(f)

    def test_csv_used_in_run(self, mock_server, tmp_path):
        csv_file = tmp_path / "users.csv"
        csv_file.write_text("username\nalice\nbob\ncharlie\n")
        scene = tmp_path / "scene_csv.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, CsvDataSet, task, constant

            users_csv = CsvDataSet(r"{csv_file}")

            class CsvUser(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                def on_start(self):
                    self.row = users_csv.next_row()

                @task(1)
                async def do(self):
                    await self.client.get("/status/200")
        """))
        result = run(scene, users=3, spawn_rate=3.0, run_time=1.5)
        assert result.totals["requests"] > 0


# ---------------------------------------------------------------------------
# 2. 前置/后置处理器
# ---------------------------------------------------------------------------

class TestProcessors:
    def test_pre_processor_decorator_marks_function(self):
        @pre_processor
        def fn(self, method, url, kwargs):
            pass
        assert getattr(fn, "_pmeter_pre_processor", False) is True

    def test_post_processor_decorator_marks_function(self):
        @post_processor
        def fn(self, response):
            pass
        assert getattr(fn, "_pmeter_post_processor", False) is True

    def test_pre_processor_called_and_can_modify_kwargs(self, env):
        injected = []

        class MyUser(HttpUser):
            host = env.host

            @pre_processor
            def inject(self, method, url, kwargs):
                injected.append(method)
                kwargs.setdefault("headers", {})["X-Test"] = "yes"
                return kwargs

            @task(1)
            async def do(self):
                await self.client.get("/status/200")

        user = MyUser(env)
        assert len(user.client._pre) == 1
        arun(user.client.get("/status/200"))
        assert injected == ["GET"]

    def test_post_processor_called_with_response(self, env):
        seen = []

        class MyUser(HttpUser):
            host = env.host

            @post_processor
            def capture(self, response):
                seen.append(response.status_code)

            @task(1)
            async def do(self):
                await self.client.get("/status/200")

        user = MyUser(env)
        arun(user.client.get("/status/200"))
        assert seen == [200]

    def test_multiple_processors(self, env):
        order = []

        class MyUser(HttpUser):
            host = env.host

            @pre_processor
            def pre1(self, method, url, kwargs):
                order.append("pre1")
                return kwargs

            @pre_processor
            def pre2(self, method, url, kwargs):
                order.append("pre2")
                return kwargs

            @post_processor
            def post1(self, response):
                order.append("post1")

            @task(1)
            async def do(self):
                await self.client.get("/status/200")

        user = MyUser(env)
        arun(user.client.get("/status/200"))
        assert "pre1" in order
        assert "pre2" in order
        assert "post1" in order


# ---------------------------------------------------------------------------
# 3. 关联提取
# ---------------------------------------------------------------------------

class TestExtractors:
    def test_extract_json_path(self, client):
        resp = arun(client.get("/json"))
        assert resp.extract_json_path("slideshow.author") == "Yours Truly"
        assert resp.extract_json_path("slideshow.items.1") == 2

    def test_extract_regex_match(self, client):
        resp = arun(client.get("/json"))
        match = resp.extract_regex(r'"author":\s*"([^"]+)"')
        assert match == "Yours Truly"

    def test_extract_regex_no_match(self, client):
        resp = arun(client.get("/json"))
        assert resp.extract_regex(r"NOPE(\d+)") is None

    def test_extract_header(self, client):
        resp = arun(client.get("/json"))
        ct = resp.extract_header("content-type")
        assert ct is not None and "json" in ct.lower()

    def test_extract_header_missing(self, client):
        resp = arun(client.get("/json"))
        assert resp.extract_header("x-does-not-exist") is None

    def test_extract_cookie(self, client):
        resp = arun(client.get("/set-cookie"))
        assert resp.extract_cookie("session") == "abc123"

    def test_extract_cookie_missing(self, client):
        resp = arun(client.get("/json"))
        assert resp.extract_cookie("nosuchcookie") is None

    def test_correlation_via_vars(self, env):
        class TokenUser(HttpUser):
            host = env.host

            @task(1)
            async def flow(self):
                resp = await self.client.get("/token")
                self.vars["token"] = resp.extract_json_path("access_token")

        user = TokenUser(env)
        arun(user.flow())
        assert user.vars["token"] == "tok-xyz-789"


# ---------------------------------------------------------------------------
# 4. 自定义检查点
# ---------------------------------------------------------------------------

class TestCheckpoints:
    def _make_user(self, env):
        u = object.__new__(HttpUser)
        u.environment = env
        u.vars = {}
        return u

    def test_check_pass_increments_passes(self, env):
        user = self._make_user(env)
        assert user.check("ok", True) is True
        check = env.stats.check_snapshot()[0]
        assert check.passes == 1
        assert check.failures == 0

    def test_check_fail_increments_failures(self, env):
        user = self._make_user(env)
        assert user.check("nope", False, message="it broke") is False
        check = env.stats.check_snapshot()[0]
        assert check.failures == 1
        assert check.last_message == "it broke"

    def test_check_accumulates_across_calls(self, env):
        user = self._make_user(env)
        for i in range(10):
            user.check("acc", i % 2 == 0)
        check = {c.name: c for c in env.stats.check_snapshot()}["acc"]
        assert check.passes == 5
        assert check.failures == 5

    def test_check_in_run_result(self, mock_server, tmp_path):
        scene = tmp_path / "scene_chk.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class Chk(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    resp = await self.client.get("/json")
                    data = resp.json()
                    self.check("has_author", "author" in data["slideshow"])
                    self.check("always_fail", False, message="intentional")
        """))
        result = run(scene, users=1, spawn_rate=1.0, run_time=2.0)
        checks = {c.name: c for c in result.checks}
        assert "has_author" in checks
        assert checks["has_author"].passes > 0
        assert checks["always_fail"].failures > 0


# ---------------------------------------------------------------------------
# 5. HTML 报告
# ---------------------------------------------------------------------------

class TestHtmlReport:
    def _make_result(self):
        stats = StatsCollector()
        stats.record_success("GET /foo", 120.5, 200)
        stats.record_success("GET /foo", 95.0, 200)
        stats.record_failure("GET /bar", 300.0, Exception("timeout"))
        stats.record_check("has_data", True)
        stats.record_check("no_error", False, "something broke")
        return RunResult(
            stats=stats.snapshot(),
            totals=stats.totals(),
            user_errors=[],
            duration_seconds=10.0,
            checks=stats.check_snapshot(),
        )

    def test_generates_file(self, tmp_path):
        result = self._make_result()
        out = generate_html_report(result, tmp_path / "report.html")
        assert out.exists()

    def test_html_contains_pmeter_title(self, tmp_path):
        result = self._make_result()
        out = generate_html_report(result, tmp_path / "report.html")
        assert "PMeter Report" in out.read_text()

    def test_html_contains_request_names(self, tmp_path):
        result = self._make_result()
        out = generate_html_report(result, tmp_path / "report.html")
        html = out.read_text()
        assert "GET /foo" in html
        assert "GET /bar" in html

    def test_html_contains_checkpoint_names(self, tmp_path):
        result = self._make_result()
        out = generate_html_report(result, tmp_path / "report.html")
        html = out.read_text()
        assert "has_data" in html
        assert "no_error" in html

    def test_html_contains_chart_js(self, tmp_path):
        result = self._make_result()
        out = generate_html_report(result, tmp_path / "report.html")
        assert "chart.js" in out.read_text().lower()

    def test_xss_escaping(self, tmp_path):
        stats = StatsCollector()
        stats.record_failure("GET <script>alert(1)</script>", 10.0, Exception("<img>"))
        result = RunResult(
            stats=stats.snapshot(),
            totals=stats.totals(),
            user_errors=[],
            duration_seconds=1.0,
            checks=[],
        )
        out = generate_html_report(result, tmp_path / "xss.html")
        html = out.read_text()
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# 6. 分布式压测
# ---------------------------------------------------------------------------

class TestDistributed:
    def test_merge_results_combines_stats(self):
        from pmeter.distributed import _merge_results
        from pmeter.stats import StatsEntry

        def make(latencies, failures):
            stats = StatsCollector()
            e = StatsEntry(name="GET /x")
            for lat in latencies:
                e.record_success(lat, 200)
            for _ in range(failures):
                e.record_failure(50.0, Exception("err"))
            stats._entries["GET /x"] = e
            return RunResult(
                stats=stats.snapshot(),
                totals=stats.totals(),
                user_errors=[],
                duration_seconds=5.0,
                checks=[],
            )

        r1 = make([100.0, 200.0], 1)
        r2 = make([150.0, 250.0], 2)
        merged = _merge_results([r1, r2])
        entry = {e.name: e for e in merged.stats}["GET /x"]
        assert entry.requests == 4 + 3
        assert entry.failures == 3
        assert merged.duration_seconds == 5.0

    def test_merge_results_merges_checks(self):
        from pmeter.distributed import _merge_results

        def make_with_check(passes, failures):
            stats = StatsCollector()
            for _ in range(passes):
                stats.record_check("c1", True)
            for _ in range(failures):
                stats.record_check("c1", False)
            return RunResult(
                stats=[],
                totals={"requests": 0, "failures": 0, "failure_rate": 0.0,
                        "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0},
                user_errors=[],
                duration_seconds=1.0,
                checks=stats.check_snapshot(),
            )

        r1 = make_with_check(3, 1)
        r2 = make_with_check(2, 2)
        merged = _merge_results([r1, r2])
        checks = {c.name: c for c in merged.checks}
        assert checks["c1"].passes == 5
        assert checks["c1"].failures == 3

    def test_run_distributed_two_workers(self, mock_server, tmp_path):
        scene = tmp_path / "scene_dist.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class W(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    await self.client.get("/status/200")
        """))
        from pmeter.distributed import run_distributed
        result = run_distributed(
            scene,
            users=2,
            spawn_rate=2.0,
            run_time=2.0,
            workers=2,
        )
        assert result.totals["requests"] > 0
        assert result.totals["failures"] == 0


# ---------------------------------------------------------------------------
# 7. Web UI
# ---------------------------------------------------------------------------

class TestWebUI:
    def test_dashboard_returns_html(self, env):
        import urllib.request
        from pmeter.web_ui import WebUIServer

        port = _free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
                html = r.read().decode()
            assert "PMeter Live Dashboard" in html
        finally:
            ui.stop()

    def test_api_stats_returns_json(self, env):
        import urllib.request
        from pmeter.web_ui import WebUIServer

        env.stats.record_success("GET /ping", 50.0, 200)
        port = _free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats") as r:
                data = json.loads(r.read())
            assert data["totals"]["requests"] == 1
            assert data["entries"][0]["name"] == "GET /ping"
            assert data["done"] is False
        finally:
            ui.stop()

    def test_api_stats_done_after_stop(self, env):
        import urllib.request
        from pmeter.web_ui import WebUIServer

        port = _free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.2)
        env.stop_event.set()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats") as r:
                data = json.loads(r.read())
            assert data["done"] is True
        finally:
            ui.stop()


# ---------------------------------------------------------------------------
# Integration: all features together
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_full_run_all_features(self, mock_server, tmp_path):
        csv_file = tmp_path / "users.csv"
        csv_file.write_text("username,role\nalice,admin\nbob,user\n")

        scene = tmp_path / "full_scene.py"
        csv_path_escaped = str(csv_file).replace("\\", "\\\\")
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, CsvDataSet, task, constant, pre_processor, post_processor

            users_csv = CsvDataSet(r"{csv_path_escaped}")

            class FullUser(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                def on_start(self):
                    self.row = users_csv.next_row()

                @pre_processor
                def add_header(self, method, url, kwargs):
                    kwargs.setdefault("headers", {{}})["X-User"] = self.row["username"]
                    return kwargs

                @post_processor
                def noop_post(self, response):
                    pass

                @task(1)
                async def json_task(self):
                    resp = await self.client.get("/json")
                    author = resp.extract_json_path("slideshow.author")
                    self.vars["author"] = author
                    self.check("author_set", author is not None)
                    self.check("status_ok", resp.status_code == 200)

                @task(1)
                async def token_task(self):
                    resp = await self.client.get("/token")
                    tok = resp.extract_json_path("access_token")
                    self.vars["token"] = tok
                    self.check("token_starts_tok", tok.startswith("tok-"))
        """))

        result = run(scene, users=2, spawn_rate=2.0, run_time=3.0)

        assert result.totals["requests"] > 0
        checks = {c.name: c for c in result.checks}
        assert checks["author_set"].passes > 0
        assert checks["token_starts_tok"].passes > 0
        assert checks["status_ok"].passes > 0
        assert result.user_errors == []

        report_path = tmp_path / "report.html"
        out = generate_html_report(result, report_path, scene=str(scene))
        assert out.exists()
        html = out.read_text()
        assert "author_set" in html
        assert "token_starts_tok" in html
        assert "GET /json" in html
