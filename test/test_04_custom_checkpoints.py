"""
Feature: 自定义检查点 (self.check)

验证检查点的通过/失败计数、消息记录、跨调用累积，
以及在 RunResult 中正确暴露。
"""
from __future__ import annotations

import textwrap

import pytest

from pmeter.runner import HttpUser, RunResult, run
from pmeter.stats import CheckEntry, StatsCollector


def _make_user(env):
    """Bypass HttpUser.__init__; only test check() logic."""
    u = object.__new__(HttpUser)
    u.environment = env
    u.vars = {}
    return u


class TestCheckReturnValue:
    """check() 应返回 condition 本身（可用于 assert 或 if）。"""

    def test_returns_true_when_passing(self, env):
        u = _make_user(env)
        assert u.check("pass", True) is True

    def test_returns_false_when_failing(self, env):
        u = _make_user(env)
        assert u.check("fail", False) is False

    def test_returns_bool_not_truthy(self, env):
        u = _make_user(env)
        result = u.check("truthy", 42)
        assert result == 42   # condition is passed through


class TestCheckCounters:
    """通过/失败计数器正确递增。"""

    def test_pass_increments_passes(self, env):
        u = _make_user(env)
        u.check("c", True)
        snap = {c.name: c for c in env.stats.check_snapshot()}
        assert snap["c"].passes == 1
        assert snap["c"].failures == 0

    def test_fail_increments_failures(self, env):
        u = _make_user(env)
        u.check("c", False)
        snap = {c.name: c for c in env.stats.check_snapshot()}
        assert snap["c"].failures == 1
        assert snap["c"].passes == 0

    def test_mixed_accumulates_correctly(self, env):
        u = _make_user(env)
        for i in range(10):
            u.check("even", i % 2 == 0)
        snap = {c.name: c for c in env.stats.check_snapshot()}
        assert snap["even"].passes == 5
        assert snap["even"].failures == 5

    def test_multiple_named_checks_tracked_separately(self, env):
        u = _make_user(env)
        u.check("alpha", True)
        u.check("beta", False)
        u.check("alpha", True)
        snap = {c.name: c for c in env.stats.check_snapshot()}
        assert snap["alpha"].passes == 2
        assert snap["beta"].failures == 1

    def test_many_passes_no_failures(self, env):
        u = _make_user(env)
        for _ in range(100):
            u.check("health", True)
        snap = {c.name: c for c in env.stats.check_snapshot()}
        assert snap["health"].passes == 100
        assert snap["health"].failures == 0


class TestCheckMessage:
    """失败时 message 被记录；通过时 message 不覆盖旧失败消息。"""

    def test_message_stored_on_failure(self, env):
        u = _make_user(env)
        u.check("c", False, message="something broke")
        snap = {c.name: c for c in env.stats.check_snapshot()}
        assert snap["c"].last_message == "something broke"

    def test_no_message_on_pass(self, env):
        u = _make_user(env)
        u.check("c", True, message="this should not appear")
        snap = {c.name: c for c in env.stats.check_snapshot()}
        # message arg on pass should not be stored
        assert snap["c"].last_message is None

    def test_latest_failure_message_wins(self, env):
        u = _make_user(env)
        u.check("c", False, message="first")
        u.check("c", False, message="second")
        snap = {c.name: c for c in env.stats.check_snapshot()}
        assert snap["c"].last_message == "second"

    def test_empty_message_on_failure(self, env):
        u = _make_user(env)
        u.check("c", False)   # no message
        snap = {c.name: c for c in env.stats.check_snapshot()}
        assert snap["c"].failures == 1
        assert snap["c"].last_message is None


class TestCheckpointsInRunResult:
    """检查点出现在 run() 返回的 RunResult.checks 中。"""

    def test_checks_present_in_result(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class C(HttpUser):
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
        assert "always_fail" in checks

    def test_passing_check_has_passes_gt_zero(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class C(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    self.check("always_pass", True)
        """))
        result = run(scene, users=1, spawn_rate=1.0, run_time=1.5)
        checks = {c.name: c for c in result.checks}
        assert checks["always_pass"].passes > 0
        assert checks["always_pass"].failures == 0

    def test_failing_check_has_failures_gt_zero(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class C(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    self.check("always_fail", False, message="expected")
        """))
        result = run(scene, users=1, spawn_rate=1.0, run_time=1.5)
        checks = {c.name: c for c in result.checks}
        assert checks["always_fail"].failures > 0

    def test_no_checks_gives_empty_list(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class C(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    await self.client.get("/status/200")
        """))
        result = run(scene, users=1, spawn_rate=1.0, run_time=1.5)
        assert result.checks == []
