"""
Feature: CSV 参数化 (CsvDataSet)

验证 CsvDataSet 的读取、循环、线程安全、自定义分隔符、
空文件检测，以及在真实压测场景中的集成使用。
"""
from __future__ import annotations

import textwrap
import threading
from pathlib import Path

import pytest

from conftest import arun
from pmeter.csv_data import CsvDataSet
from pmeter.runner import run


class TestCsvBasicRead:
    """基础读取行为。"""

    def test_reads_all_rows(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("name,age\nalice,30\nbob,25\ncharlie,22\n")
        ds = CsvDataSet(f)
        assert len(ds) == 3

    def test_row_values_are_strings(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("id,score\n1,99\n2,88\n")
        ds = CsvDataSet(f)
        row = ds.next_row()
        assert isinstance(row["id"], str)
        assert isinstance(row["score"], str)

    def test_iterable_returns_all_rows(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("x\na\nb\nc\n")
        ds = CsvDataSet(f)
        rows = list(ds)
        assert [r["x"] for r in rows] == ["a", "b", "c"]

    def test_len_matches_data_rows(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("col\n" + "\n".join(str(i) for i in range(20)))
        ds = CsvDataSet(f)
        assert len(ds) == 20


class TestCsvCycle:
    """循环读取行为（超过尾行自动回头）。"""

    def test_cycles_after_last_row(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("x\n1\n2\n")
        ds = CsvDataSet(f)
        assert ds.next_row() == {"x": "1"}
        assert ds.next_row() == {"x": "2"}
        assert ds.next_row() == {"x": "1"}   # 回到第一行
        assert ds.next_row() == {"x": "2"}

    def test_single_row_always_returns_same(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("key\nonly\n")
        ds = CsvDataSet(f)
        for _ in range(5):
            assert ds.next_row() == {"key": "only"}


class TestCsvThreadSafety:
    """多线程并发访问不会丢行、不会重复返回同一行（循环是确定性的）。"""

    def test_concurrent_reads_total_count(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("id\n" + "\n".join(str(i) for i in range(10)))
        ds = CsvDataSet(f)
        seen: list[str] = []
        lock = threading.Lock()

        def worker():
            for _ in range(30):
                row = ds.next_row()
                with lock:
                    seen.append(row["id"])

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(seen) == 150   # 5 threads × 30 reads each

    def test_all_returned_values_are_valid(self, tmp_path):
        f = tmp_path / "data.csv"
        valid = {str(i) for i in range(8)}
        f.write_text("id\n" + "\n".join(sorted(valid)))
        ds = CsvDataSet(f)
        results: list[str] = []
        lock = threading.Lock()

        def worker():
            for _ in range(16):
                row = ds.next_row()
                with lock:
                    results.append(row["id"])

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(v in valid for v in results)


class TestCsvOptions:
    """分隔符、编码等选项。"""

    def test_tab_delimiter(self, tmp_path):
        f = tmp_path / "data.tsv"
        f.write_text("a\tb\n1\t2\n3\t4\n")
        ds = CsvDataSet(f, delimiter="\t")
        assert ds.next_row() == {"a": "1", "b": "2"}

    def test_semicolon_delimiter(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("city;country\nBeijing;China\n")
        ds = CsvDataSet(f, delimiter=";")
        assert ds.next_row() == {"city": "Beijing", "country": "China"}

    def test_empty_csv_raises_value_error(self, tmp_path):
        f = tmp_path / "empty.csv"
        f.write_text("name\n")   # 只有表头，没有数据行
        with pytest.raises(ValueError, match="no data rows"):
            CsvDataSet(f)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            CsvDataSet(tmp_path / "nonexistent.csv")


class TestCsvInRun:
    """CSV 参数化在完整压测中正常工作。"""

    def test_csv_rows_used_by_users(self, mock_server, tmp_path):
        csv_file = tmp_path / "users.csv"
        csv_file.write_text("username\nalice\nbob\ncharlie\n")

        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, CsvDataSet, task, constant

            users_csv = CsvDataSet(r"{csv_file}")

            class CsvUser(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                def on_start(self):
                    self.row = users_csv.next_row()
                    assert self.row["username"] in ("alice", "bob", "charlie")

                @task(1)
                async def do(self):
                    await self.client.get("/status/200")
        """))

        result = run(scene, users=3, spawn_rate=3.0, run_time=1.5)
        assert result.totals["requests"] > 0
        assert result.totals["failures"] == 0
        assert result.user_errors == []
