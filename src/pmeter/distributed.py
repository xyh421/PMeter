from __future__ import annotations

import math
import multiprocessing
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pmeter.runner import RunResult
    from pmeter.stats import StatsEntry, CheckEntry


def _worker_fn(
    queue: multiprocessing.Queue,
    scene_file: str,
    users: int,
    spawn_rate: float,
    run_time: float,
    host: str | None,
) -> None:
    # Imported inside worker to avoid issues on Windows spawn
    from pmeter.runner import run

    try:
        result = run(
            scene_file,
            users=users,
            spawn_rate=spawn_rate,
            run_time=run_time,
            host=host,
        )
        queue.put(("ok", result))
    except Exception as exc:
        queue.put(("error", str(exc)))


def run_distributed(
    scene_file: str | Path,
    *,
    users: int,
    spawn_rate: float,
    run_time: float,
    host: str | None = None,
    workers: int,
) -> "RunResult":
    """Spawn *workers* processes and merge their results.

    Each worker runs ``users // workers`` virtual users (with the remainder
    distributed round-robin to the first few workers).
    """
    if workers <= 0:
        raise ValueError("workers must be greater than 0")

    scene_file = str(Path(scene_file).resolve())
    base, rem = divmod(users, workers)
    user_counts = [base + (1 if i < rem else 0) for i in range(workers)]
    per_worker_rate = max(spawn_rate / workers, 0.1)

    ctx = multiprocessing.get_context("spawn")
    queue: multiprocessing.Queue = ctx.Queue()

    processes = []
    for n in user_counts:
        if n == 0:
            continue
        p = ctx.Process(
            target=_worker_fn,
            args=(queue, scene_file, n, per_worker_rate, run_time, host),
            daemon=True,
        )
        processes.append(p)
        p.start()

    results = []
    errors = []
    for _ in processes:
        status, payload = queue.get(timeout=run_time + 120)
        if status == "ok":
            results.append(payload)
        else:
            errors.append(payload)

    for p in processes:
        p.join(timeout=10)

    if not results:
        raise RuntimeError(f"all workers failed: {errors}")

    merged = _merge_results(results)
    if errors:
        merged.user_errors.extend([f"[worker error] {e}" for e in errors])
    return merged


def _merge_results(results: list["RunResult"]) -> "RunResult":
    from pmeter.runner import RunResult

    merged_entries: dict[str, "StatsEntry"] = {}
    merged_checks: dict[str, "CheckEntry"] = {}
    all_errors: list[str] = []
    max_duration = 0.0

    for result in results:
        all_errors.extend(result.user_errors)
        max_duration = max(max_duration, result.duration_seconds)

        for entry in result.stats:
            if entry.name in merged_entries:
                _merge_stat_entry(merged_entries[entry.name], entry)
            else:
                merged_entries[entry.name] = entry

        for check in getattr(result, "checks", []):
            if check.name in merged_checks:
                existing = merged_checks[check.name]
                existing.passes += check.passes
                existing.failures += check.failures
                if check.last_message:
                    existing.last_message = check.last_message
            else:
                merged_checks[check.name] = check

    # Rebuild totals from merged stats
    all_entries = list(merged_entries.values())
    total_requests = sum(e.requests for e in all_entries)
    total_failures = sum(e.failures for e in all_entries)
    all_latencies = sorted(lat for e in all_entries for lat in e.latencies)

    totals = {
        "requests": total_requests,
        "failures": total_failures,
        "failure_rate": (total_failures / total_requests) if total_requests else 0.0,
        "p50_ms": _pct(all_latencies, 0.50),
        "p95_ms": _pct(all_latencies, 0.95),
        "p99_ms": _pct(all_latencies, 0.99),
    }

    return RunResult(
        stats=all_entries,
        totals=totals,
        user_errors=all_errors,
        duration_seconds=max_duration,
        checks=list(merged_checks.values()),
    )


def _merge_stat_entry(base: "StatsEntry", extra: "StatsEntry") -> None:
    base.requests += extra.requests
    base.failures += extra.failures
    base.total_ms += extra.total_ms
    base.min_ms = min(base.min_ms, extra.min_ms)
    base.max_ms = max(base.max_ms, extra.max_ms)
    base.latencies.extend(extra.latencies)
    for code, count in extra.status_codes.items():
        base.status_codes[code] = base.status_codes.get(code, 0) + count
    if extra.last_error:
        base.last_error = extra.last_error


def _pct(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, math.ceil(len(values) * ratio) - 1)
    return values[index]
