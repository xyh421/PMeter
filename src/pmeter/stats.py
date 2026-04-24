from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StatsEntry:
    name: str
    requests: int = 0
    failures: int = 0
    total_ms: float = 0.0
    min_ms: float = math.inf
    max_ms: float = 0.0
    latencies: list[float] = field(default_factory=list)
    status_codes: dict[int, int] = field(default_factory=dict)
    last_error: str | None = None

    def record_success(self, elapsed_ms: float, status_code: int) -> None:
        self.requests += 1
        self.total_ms += elapsed_ms
        self.min_ms = min(self.min_ms, elapsed_ms)
        self.max_ms = max(self.max_ms, elapsed_ms)
        self.latencies.append(elapsed_ms)
        self.status_codes[status_code] = self.status_codes.get(status_code, 0) + 1

    def record_failure(self, elapsed_ms: float, error: Exception) -> None:
        self.requests += 1
        self.failures += 1
        self.total_ms += elapsed_ms
        self.min_ms = min(self.min_ms, elapsed_ms)
        self.max_ms = max(self.max_ms, elapsed_ms)
        self.latencies.append(elapsed_ms)
        self.last_error = str(error)

    def reclassify_success_as_failure(
        self,
        elapsed_ms: float,
        status_code: int,
        error: Exception,
    ) -> None:
        self.failures += 1
        if self.status_codes.get(status_code):
            self.status_codes[status_code] -= 1
            if self.status_codes[status_code] <= 0:
                del self.status_codes[status_code]
        self.last_error = str(error)

    @property
    def avg_ms(self) -> float:
        if self.requests == 0:
            return 0.0
        return self.total_ms / self.requests

    def percentile(self, ratio: float) -> float:
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        index = min(len(ordered) - 1, math.ceil(len(ordered) * ratio) - 1)
        return ordered[index]


@dataclass
class CheckEntry:
    """Tracks pass/fail counts for a named checkpoint."""

    name: str
    passes: int = 0
    failures: int = 0
    last_message: str | None = None


class StatsCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, StatsEntry] = {}
        self._check_entries: dict[str, CheckEntry] = {}

    def record_success(self, name: str, elapsed_ms: float, status_code: int) -> None:
        with self._lock:
            entry = self._entries.setdefault(name, StatsEntry(name=name))
            entry.record_success(elapsed_ms, status_code)

    def record_failure(self, name: str, elapsed_ms: float, error: Exception) -> None:
        with self._lock:
            entry = self._entries.setdefault(name, StatsEntry(name=name))
            entry.record_failure(elapsed_ms, error)

    def reclassify_success_as_failure(
        self,
        name: str,
        elapsed_ms: float,
        status_code: int,
        error: Exception,
    ) -> None:
        with self._lock:
            entry = self._entries.setdefault(name, StatsEntry(name=name))
            entry.reclassify_success_as_failure(elapsed_ms, status_code, error)

    def record_check(self, name: str, passed: bool, message: str = "") -> None:
        """Record a checkpoint result (自定义检查点)."""
        with self._lock:
            entry = self._check_entries.setdefault(name, CheckEntry(name=name))
            if passed:
                entry.passes += 1
            else:
                entry.failures += 1
                if message:
                    entry.last_message = message

    def snapshot(self) -> list[StatsEntry]:
        with self._lock:
            return [self._clone_entry(entry) for entry in self._entries.values()]

    def check_snapshot(self) -> list[CheckEntry]:
        with self._lock:
            return list(self._check_entries.values())

    def totals(self) -> dict[str, Any]:
        entries = self.snapshot()
        total_requests = sum(entry.requests for entry in entries)
        total_failures = sum(entry.failures for entry in entries)
        all_latencies: list[float] = []
        for entry in entries:
            all_latencies.extend(entry.latencies)
        all_latencies.sort()
        return {
            "requests": total_requests,
            "failures": total_failures,
            "failure_rate": (total_failures / total_requests) if total_requests else 0.0,
            "p50_ms": self._percentile(all_latencies, 0.50),
            "p95_ms": self._percentile(all_latencies, 0.95),
            "p99_ms": self._percentile(all_latencies, 0.99),
        }

    def _percentile(self, values: list[float], ratio: float) -> float:
        if not values:
            return 0.0
        index = min(len(values) - 1, math.ceil(len(values) * ratio) - 1)
        return values[index]

    def _clone_entry(self, entry: StatsEntry) -> StatsEntry:
        clone = StatsEntry(name=entry.name)
        clone.requests = entry.requests
        clone.failures = entry.failures
        clone.total_ms = entry.total_ms
        clone.min_ms = entry.min_ms
        clone.max_ms = entry.max_ms
        clone.latencies = list(entry.latencies)
        clone.status_codes = dict(entry.status_codes)
        clone.last_error = entry.last_error
        return clone
