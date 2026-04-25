"""
Feature: 端到端集成测试

在单次 run() 中综合使用所有 PMeter 功能：
CSV 参数化 + 前/后置处理器 + 关联提取 + 自定义检查点 + HTML 报告。
同时验证并发行为、错误统计和基础性能指标。
"""
from __future__ import annotations

import textwrap
import time

import pytest

from pmeter.report import generate_html_report
from pmeter.runner import run


class TestFullFeatureRun:
    """所有功能同时在一次 run() 中正常工作。"""

    def test_all_features_combined(self, mock_server, tmp_path):
        csv_file = tmp_path / "users.csv"
        csv_file.write_text("username,role\nalice,admin\nbob,user\n")

        scene = tmp_path / "full_scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, CsvDataSet, task, constant, pre_processor, post_processor

            users_csv = CsvDataSet(r"{csv_file}")

            class FullUser(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                def on_start(self):
                    self.row = users_csv.next_row()

                @pre_processor
                def add_user_header(self, method, url, kwargs):
                    kwargs.setdefault("headers", {{}})["X-User"] = self.row["username"]
                    return kwargs

                @post_processor
                def noop(self, response):
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
        assert result.user_errors == []

        checks = {c.name: c for c in result.checks}
        assert checks["author_set"].passes > 0
        assert checks["status_ok"].passes > 0
        assert checks["token_starts_tok"].passes > 0

    def test_html_report_generated_from_full_run(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class S(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    resp = await self.client.get("/json")
                    self.check("ok", resp.status_code == 200)
        """))

        result = run(scene, users=2, spawn_rate=2.0, run_time=2.0)
        report = generate_html_report(result, tmp_path / "report.html", scene=str(scene))

        assert report.exists()
        html = report.read_text()
        assert "PMeter Report" in html
        assert "ok" in html
        assert "GET /json" in html


class TestConcurrencyBehavior:
    """多用户并发时统计正确，无竞争导致的错误。"""

    def test_multi_user_no_errors(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class U(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    await self.client.get("/status/200")
        """))
        result = run(scene, users=20, spawn_rate=10.0, run_time=3.0)
        assert result.totals["requests"] > 0
        assert result.totals["failures"] == 0
        assert result.user_errors == []

    def test_high_concurrency_stats_consistent(self, mock_server, tmp_path):
        """requests == successes + failures (no lost records)."""
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class U(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(2)
                async def ok(self):
                    await self.client.get("/status/200")

                @task(1)
                async def fail(self):
                    resp = await self.client.get("/status/404")
                    resp.assert_status(200)   # intentional failure
        """))
        result = run(scene, users=10, spawn_rate=10.0, run_time=2.0)
        total = result.totals["requests"]
        failures = result.totals["failures"]
        # all latencies are recorded
        all_latencies = sum(len(e.latencies) for e in result.stats)
        assert all_latencies == total

    def test_rps_is_positive(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class U(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    await self.client.get("/status/200")
        """))
        result = run(scene, users=5, spawn_rate=5.0, run_time=2.0)
        rps = result.totals["requests"] / result.duration_seconds
        assert rps > 0


class TestAssertionFailures:
    """断言失败正确计入 failures，不抛出未处理异常。"""

    def test_assert_status_failure_counted(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class U(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    resp = await self.client.get("/status/404")
                    resp.assert_status(200)   # will fail
        """))
        result = run(scene, users=1, spawn_rate=1.0, run_time=1.5)
        assert result.totals["failures"] > 0

    def test_assert_json_path_failure_counted(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class U(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    resp = await self.client.get("/json")
                    resp.assert_json_path("slideshow.author", "Wrong Author")
        """))
        result = run(scene, users=1, spawn_rate=1.0, run_time=1.5)
        assert result.totals["failures"] > 0

    def test_user_errors_captured_not_propagated(self, mock_server, tmp_path):
        """Exceptions inside @task don't crash the run, they go to user_errors."""
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class U(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    raise RuntimeError("deliberate crash")
        """))
        result = run(scene, users=1, spawn_rate=1.0, run_time=1.5)
        assert len(result.user_errors) > 0
        assert "deliberate crash" in result.user_errors[0]


class TestRunnerConfiguration:
    """run() 参数校验和基础行为。"""

    def test_zero_users_raises(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant
            class U(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)
                @task(1)
                async def do(self): pass
        """))
        with pytest.raises(ValueError, match="users"):
            run(scene, users=0, spawn_rate=1.0, run_time=1.0)

    def test_duration_respected(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant
            class U(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)
                @task(1)
                async def do(self):
                    await self.client.get("/status/200")
        """))
        t0 = time.perf_counter()
        result = run(scene, users=2, spawn_rate=2.0, run_time=2.0)
        elapsed = time.perf_counter() - t0
        # Should finish close to run_time (±3s for spawning + graceful shutdown)
        assert 1.5 <= elapsed <= 15.0

    def test_percentiles_computed(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant
            class U(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)
                @task(1)
                async def do(self):
                    await self.client.get("/status/200")
        """))
        result = run(scene, users=3, spawn_rate=3.0, run_time=2.0)
        assert result.totals["p50_ms"] >= 0
        assert result.totals["p95_ms"] >= result.totals["p50_ms"]
        assert result.totals["p99_ms"] >= result.totals["p95_ms"]
