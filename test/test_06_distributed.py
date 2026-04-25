"""
Feature: 分布式压测 (run_distributed / --workers N)

验证用户数分配、多 worker 结果合并（stats / checks / errors），
以及真实双进程分布式运行。
"""
from __future__ import annotations

import math
import textwrap

import pytest

from pmeter.distributed import _merge_results, run_distributed
from pmeter.runner import RunResult
from pmeter.stats import CheckEntry, StatsCollector, StatsEntry


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_result_with_stats(
    name: str,
    latencies: list[float],
    failures: int = 0,
    duration: float = 5.0,
) -> RunResult:
    stats = StatsCollector()
    e = StatsEntry(name=name)
    for lat in latencies:
        e.record_success(lat, 200)
    for _ in range(failures):
        e.record_failure(50.0, Exception("err"))
    stats._entries[name] = e
    return RunResult(
        stats=stats.snapshot(),
        totals=stats.totals(),
        user_errors=[],
        duration_seconds=duration,
        checks=[],
    )


def _make_result_with_checks(checks: dict[str, tuple[int, int]]) -> RunResult:
    """checks = {name: (passes, failures)}"""
    stats = StatsCollector()
    for name, (p, f) in checks.items():
        for _ in range(p):
            stats.record_check(name, True)
        for _ in range(f):
            stats.record_check(name, False)
    return RunResult(
        stats=[],
        totals={"requests": 0, "failures": 0, "failure_rate": 0.0,
                "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0},
        user_errors=[],
        duration_seconds=1.0,
        checks=stats.check_snapshot(),
    )


# ---------------------------------------------------------------------------
# Unit tests: _merge_results
# ---------------------------------------------------------------------------

class TestMergeStats:
    """合并后 StatsEntry 数据正确聚合。"""

    def test_request_counts_summed(self):
        r1 = _make_result_with_stats("GET /x", [100.0, 200.0])
        r2 = _make_result_with_stats("GET /x", [150.0])
        merged = _merge_results([r1, r2])
        entry = {e.name: e for e in merged.stats}["GET /x"]
        assert entry.requests == 3

    def test_failure_counts_summed(self):
        r1 = _make_result_with_stats("GET /x", [100.0], failures=2)
        r2 = _make_result_with_stats("GET /x", [100.0], failures=3)
        merged = _merge_results([r1, r2])
        entry = {e.name: e for e in merged.stats}["GET /x"]
        assert entry.failures == 5

    def test_latencies_combined(self):
        r1 = _make_result_with_stats("GET /x", [50.0, 100.0])
        r2 = _make_result_with_stats("GET /x", [200.0, 400.0])
        merged = _merge_results([r1, r2])
        entry = {e.name: e for e in merged.stats}["GET /x"]
        assert len(entry.latencies) == 4

    def test_min_ms_is_global_min(self):
        r1 = _make_result_with_stats("GET /x", [500.0, 300.0])
        r2 = _make_result_with_stats("GET /x", [100.0, 800.0])
        merged = _merge_results([r1, r2])
        entry = {e.name: e for e in merged.stats}["GET /x"]
        assert entry.min_ms == pytest.approx(100.0)

    def test_max_ms_is_global_max(self):
        r1 = _make_result_with_stats("GET /x", [100.0, 200.0])
        r2 = _make_result_with_stats("GET /x", [150.0, 999.0])
        merged = _merge_results([r1, r2])
        entry = {e.name: e for e in merged.stats}["GET /x"]
        assert entry.max_ms == pytest.approx(999.0)

    def test_duration_is_maximum(self):
        r1 = _make_result_with_stats("GET /x", [100.0], duration=3.0)
        r2 = _make_result_with_stats("GET /x", [100.0], duration=7.0)
        merged = _merge_results([r1, r2])
        assert merged.duration_seconds == pytest.approx(7.0)

    def test_different_endpoints_kept_separate(self):
        r1 = _make_result_with_stats("GET /a", [100.0])
        r2 = _make_result_with_stats("GET /b", [200.0])
        merged = _merge_results([r1, r2])
        names = {e.name for e in merged.stats}
        assert names == {"GET /a", "GET /b"}

    def test_three_workers_merged(self):
        results = [_make_result_with_stats("GET /x", [float(i * 10)]) for i in range(1, 4)]
        merged = _merge_results(results)
        entry = {e.name: e for e in merged.stats}["GET /x"]
        assert entry.requests == 3


class TestMergeChecks:
    """合并后检查点数据正确聚合。"""

    def test_passes_summed(self):
        r1 = _make_result_with_checks({"c1": (3, 0)})
        r2 = _make_result_with_checks({"c1": (2, 0)})
        merged = _merge_results([r1, r2])
        checks = {c.name: c for c in merged.checks}
        assert checks["c1"].passes == 5

    def test_failures_summed(self):
        r1 = _make_result_with_checks({"c1": (0, 1)})
        r2 = _make_result_with_checks({"c1": (0, 2)})
        merged = _merge_results([r1, r2])
        checks = {c.name: c for c in merged.checks}
        assert checks["c1"].failures == 3

    def test_mixed_passes_and_failures(self):
        r1 = _make_result_with_checks({"c1": (3, 1)})
        r2 = _make_result_with_checks({"c1": (2, 2)})
        merged = _merge_results([r1, r2])
        checks = {c.name: c for c in merged.checks}
        assert checks["c1"].passes == 5
        assert checks["c1"].failures == 3

    def test_multiple_check_names_tracked(self):
        r1 = _make_result_with_checks({"alpha": (2, 0), "beta": (0, 1)})
        r2 = _make_result_with_checks({"alpha": (1, 0), "beta": (0, 1)})
        merged = _merge_results([r1, r2])
        checks = {c.name: c for c in merged.checks}
        assert checks["alpha"].passes == 3
        assert checks["beta"].failures == 2


class TestMergeErrors:
    """多 worker 的 user_errors 合并到主结果。"""

    def test_errors_from_all_workers_collected(self):
        def _r(errs):
            return RunResult(
                stats=[], totals={"requests": 0, "failures": 0, "failure_rate": 0.0,
                                  "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0},
                user_errors=errs, duration_seconds=1.0, checks=[],
            )
        merged = _merge_results([_r(["err-a"]), _r(["err-b", "err-c"])])
        assert "err-a" in merged.user_errors
        assert "err-b" in merged.user_errors
        assert "err-c" in merged.user_errors


# ---------------------------------------------------------------------------
# Integration test: actual multi-process run
# ---------------------------------------------------------------------------

class TestDistributedRun:
    def test_two_workers_produce_requests(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class W(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    await self.client.get("/status/200")
        """))
        result = run_distributed(scene, users=2, spawn_rate=2.0, run_time=2.0, workers=2)
        assert result.totals["requests"] > 0
        assert result.totals["failures"] == 0

    def test_distributed_checks_merged(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class W(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    await self.client.get("/status/200")
                    self.check("ok", True)
        """))
        result = run_distributed(scene, users=2, spawn_rate=2.0, run_time=2.0, workers=2)
        checks = {c.name: c for c in result.checks}
        assert "ok" in checks
        assert checks["ok"].passes > 0

    def test_single_worker_same_as_run(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant

            class W(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @task(1)
                async def do(self):
                    await self.client.get("/status/200")
        """))
        result = run_distributed(scene, users=1, spawn_rate=1.0, run_time=1.5, workers=1)
        assert result.totals["requests"] > 0
