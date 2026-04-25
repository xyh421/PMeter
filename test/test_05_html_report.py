"""
Feature: HTML 报告 (generate_html_report)

验证报告文件生成、内容完整性、Chart.js 图表注入、
XSS 转义安全，以及检查点表格渲染。
"""
from __future__ import annotations

import pytest

from pmeter.report import generate_html_report
from pmeter.runner import RunResult
from pmeter.stats import StatsCollector


def _make_result(
    successes: list[tuple[str, float, int]] | None = None,
    failures: list[tuple[str, float, str]] | None = None,
    checks: list[tuple[str, bool, str]] | None = None,
    duration: float = 10.0,
) -> RunResult:
    stats = StatsCollector()
    for name, ms, code in (successes or []):
        stats.record_success(name, ms, code)
    for name, ms, err in (failures or []):
        stats.record_failure(name, ms, Exception(err))
    for name, passed, msg in (checks or []):
        stats.record_check(name, passed, msg)
    return RunResult(
        stats=stats.snapshot(),
        totals=stats.totals(),
        user_errors=[],
        duration_seconds=duration,
        checks=stats.check_snapshot(),
    )


class TestReportFileOutput:
    """报告文件正确写出。"""

    def test_file_is_created(self, tmp_path):
        result = _make_result(successes=[("GET /foo", 100.0, 200)])
        out = generate_html_report(result, tmp_path / "report.html")
        assert out.exists()

    def test_returns_path_object(self, tmp_path):
        from pathlib import Path
        result = _make_result(successes=[("GET /foo", 100.0, 200)])
        out = generate_html_report(result, tmp_path / "report.html")
        assert isinstance(out, Path)

    def test_file_not_empty(self, tmp_path):
        result = _make_result(successes=[("GET /foo", 100.0, 200)])
        out = generate_html_report(result, tmp_path / "report.html")
        assert out.stat().st_size > 0

    def test_file_is_utf8(self, tmp_path):
        result = _make_result(successes=[("GET /测试", 100.0, 200)])
        out = generate_html_report(result, tmp_path / "report.html")
        html = out.read_text(encoding="utf-8")
        assert "测试" in html


class TestReportContent:
    """报告 HTML 包含关键内容。"""

    def test_contains_pmeter_title(self, tmp_path):
        result = _make_result(successes=[("GET /x", 50.0, 200)])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "PMeter Report" in html

    def test_contains_request_name(self, tmp_path):
        result = _make_result(successes=[("GET /health", 50.0, 200)])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "GET /health" in html

    def test_contains_failure_name(self, tmp_path):
        result = _make_result(failures=[("POST /submit", 200.0, "timeout")])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "POST /submit" in html

    def test_contains_duration(self, tmp_path):
        result = _make_result(successes=[("GET /x", 50.0, 200)], duration=42.0)
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "42" in html

    def test_multiple_endpoints_all_present(self, tmp_path):
        result = _make_result(successes=[
            ("GET /a", 10.0, 200),
            ("GET /b", 20.0, 200),
            ("POST /c", 30.0, 201),
        ])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "GET /a" in html
        assert "GET /b" in html
        assert "POST /c" in html


class TestReportCharts:
    """Chart.js 图表数据正确嵌入。"""

    def test_chartjs_script_tag_present(self, tmp_path):
        result = _make_result(successes=[("GET /x", 50.0, 200)])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "chart.js" in html.lower()

    def test_rt_data_json_in_script(self, tmp_path):
        result = _make_result(successes=[("GET /api", 100.0, 200)])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "rtData" in html

    def test_rf_data_json_in_script(self, tmp_path):
        result = _make_result(successes=[("GET /api", 100.0, 200)])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "rfData" in html

    def test_canvas_elements_present(self, tmp_path):
        result = _make_result(successes=[("GET /x", 50.0, 200)])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "rtChart" in html
        assert "rfChart" in html


class TestReportCheckpoints:
    """检查点专属表格正确渲染。"""

    def test_checkpoint_section_present_when_checks_exist(self, tmp_path):
        result = _make_result(
            successes=[("GET /x", 50.0, 200)],
            checks=[("stock > 0", True, ""), ("price valid", False, "price=0")],
        )
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "Checkpoints" in html

    def test_checkpoint_names_in_html(self, tmp_path):
        result = _make_result(
            checks=[("has_data", True, ""), ("no_error", False, "broke")],
        )
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "has_data" in html
        assert "no_error" in html

    def test_no_checkpoint_section_when_empty(self, tmp_path):
        result = _make_result(successes=[("GET /x", 50.0, 200)])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "Checkpoints" not in html


class TestReportXSS:
    """请求名、错误信息中的特殊字符必须被 HTML 转义。"""

    def test_script_tag_in_name_is_escaped(self, tmp_path):
        result = _make_result(failures=[("GET <script>alert(1)</script>", 10.0, "err")])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html

    def test_angle_brackets_in_error_escaped(self, tmp_path):
        result = _make_result(failures=[("GET /x", 10.0, "<img src=x onerror=alert()>")])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "<img src=x" not in html

    def test_ampersand_in_name_escaped(self, tmp_path):
        result = _make_result(successes=[("GET /a&b", 50.0, 200)])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "GET /a&amp;b" in html

    def test_chart_json_uses_unicode_escapes(self, tmp_path):
        """JSON embedded in <script> uses \\u003c etc. to prevent injection."""
        result = _make_result(failures=[("GET </script>", 10.0, "err")])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "</script>" not in html.split("<script")[1].split("</script")[0]

    def test_double_quote_in_name_escaped(self, tmp_path):
        result = _make_result(successes=[('GET /a"b', 50.0, 200)])
        html = generate_html_report(result, tmp_path / "r.html").read_text()
        assert "&quot;" in html
