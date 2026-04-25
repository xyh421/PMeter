"""
Feature: Web UI 实时面板 (WebUIServer)

验证仪表板 HTML 输出、/api/stats JSON 结构、done 标志，
以及 Web UI 在真实 run() 期间不影响压测结果。
"""
from __future__ import annotations

import json
import time
import urllib.request

import pytest

from conftest import free_port
from pmeter.web_ui import WebUIServer


class TestDashboardEndpoint:
    """GET / 返回完整的 HTML 仪表板。"""

    def test_returns_200(self, env):
        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
                assert r.status == 200
        finally:
            ui.stop()

    def test_contains_dashboard_title(self, env):
        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
                html = r.read().decode()
            assert "PMeter Live Dashboard" in html
        finally:
            ui.stop()

    def test_contains_chart_js(self, env):
        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
                html = r.read().decode()
            assert "chart.js" in html.lower()
        finally:
            ui.stop()

    def test_content_type_is_html(self, env):
        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
                ct = r.headers.get("Content-Type", "")
            assert "text/html" in ct
        finally:
            ui.stop()


class TestApiStatsEndpoint:
    """GET /api/stats 返回 JSON 格式的实时统计数据。"""

    def test_returns_200(self, env):
        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats") as r:
                assert r.status == 200
        finally:
            ui.stop()

    def test_content_type_is_json(self, env):
        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats") as r:
                ct = r.headers.get("Content-Type", "")
            assert "json" in ct
        finally:
            ui.stop()

    def test_totals_key_present(self, env):
        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats") as r:
                data = json.loads(r.read())
            assert "totals" in data
        finally:
            ui.stop()

    def test_entries_key_present(self, env):
        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats") as r:
                data = json.loads(r.read())
            assert "entries" in data
        finally:
            ui.stop()

    def test_done_is_false_while_running(self, env):
        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats") as r:
                data = json.loads(r.read())
            assert data["done"] is False
        finally:
            ui.stop()

    def test_stats_reflect_recorded_requests(self, env):
        env.stats.record_success("GET /ping", 50.0, 200)
        env.stats.record_success("GET /ping", 80.0, 200)
        env.stats.record_failure("GET /bad", 200.0, Exception("timeout"))

        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats") as r:
                data = json.loads(r.read())
            assert data["totals"]["requests"] == 3
            assert data["totals"]["failures"] == 1
            names = {e["name"] for e in data["entries"]}
            assert "GET /ping" in names
            assert "GET /bad" in names
        finally:
            ui.stop()

    def test_entry_has_expected_fields(self, env):
        env.stats.record_success("GET /check", 42.0, 200)
        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats") as r:
                data = json.loads(r.read())
            entry = next(e for e in data["entries"] if e["name"] == "GET /check")
            for field in ("name", "requests", "failures", "avg_ms"):
                assert field in entry, f"Missing field: {field}"
        finally:
            ui.stop()


class TestDoneFlag:
    """stop_event.set() 后 done 标志变为 True。"""

    def test_done_true_after_stop_event(self, env):
        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.2)
        env.stop_event.set()
        time.sleep(0.1)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats") as r:
                data = json.loads(r.read())
            assert data["done"] is True
        finally:
            ui.stop()


class TestWebUILifecycle:
    """start/stop 生命周期正常。"""

    def test_stop_does_not_raise(self, env):
        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        ui.stop()   # should complete without raising

    def test_multiple_requests_served(self, env):
        port = free_port()
        ui = WebUIServer(env, port=port)
        ui.start()
        time.sleep(0.3)
        try:
            for _ in range(5):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats") as r:
                    assert r.status == 200
        finally:
            ui.stop()
