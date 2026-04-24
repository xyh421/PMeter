from __future__ import annotations

import argparse
from pathlib import Path

from pmeter.runner import run


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pmeter",
        description="Python-first API performance testing tool.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a Python scenario file.")
    run_parser.add_argument("scene", type=Path, help="Path to the Python scene file.")
    run_parser.add_argument("--users", type=int, default=1, help="Number of concurrent users.")
    run_parser.add_argument("--spawn-rate", type=float, default=1.0, help="Users spawned per second.")
    run_parser.add_argument("--run-time", type=str, default="30s", help="Run time e.g. 30s, 5m, 1h.")
    run_parser.add_argument("--host", type=str, default=None, help="Base host for relative URLs.")

    # HTML report (HTML 报告)
    run_parser.add_argument(
        "--html-report",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write an HTML report to PATH after the run.",
    )

    # Distributed mode (分布式压测)
    run_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="Number of worker processes for distributed load testing.",
    )

    # Web UI (Web UI)
    run_parser.add_argument(
        "--web-ui",
        action="store_true",
        help="Start a live Web UI dashboard during the run.",
    )
    run_parser.add_argument(
        "--web-port",
        type=int,
        default=8089,
        help="Port for the Web UI (default: 8089).",
    )

    args = parser.parse_args()

    if args.command == "run":
        run_time = parse_duration(args.run_time)
        web_ui_port = args.web_port if args.web_ui else None

        if args.workers > 1:
            from pmeter.distributed import run_distributed
            result = run_distributed(
                args.scene,
                users=args.users,
                spawn_rate=args.spawn_rate,
                run_time=run_time,
                host=args.host,
                workers=args.workers,
            )
        else:
            result = run(
                args.scene,
                users=args.users,
                spawn_rate=args.spawn_rate,
                run_time=run_time,
                host=args.host,
                web_ui_port=web_ui_port,
            )

        print_report(result)

        if args.html_report:
            from pmeter.report import generate_html_report
            out = generate_html_report(result, args.html_report, scene=str(args.scene))
            print(f"\nHTML report: {out}")

        return 1 if result.totals["failures"] or result.user_errors else 0

    return 0


def parse_duration(value: str) -> float:
    unit = value[-1].lower()
    amount = float(value[:-1])
    if unit == "s":
        return amount
    if unit == "m":
        return amount * 60
    if unit == "h":
        return amount * 3600
    raise ValueError(f"unsupported duration: {value!r}")


def print_report(result) -> None:
    print("\n" + "=" * 60)
    print("PMeter Summary")
    print("=" * 60)
    print(f"Duration:     {result.duration_seconds:.2f}s")
    print(f"Requests:     {result.totals['requests']}")
    print(f"Failures:     {result.totals['failures']}")
    print(f"Failure Rate: {result.totals['failure_rate']:.2%}")
    rps = result.totals["requests"] / result.duration_seconds if result.duration_seconds else 0
    print(f"RPS:          {rps:.1f}")
    print(f"P50:          {result.totals['p50_ms']:.2f}ms")
    print(f"P95:          {result.totals['p95_ms']:.2f}ms")
    print(f"P99:          {result.totals['p99_ms']:.2f}ms")

    print("\nRequest Stats")
    print("-" * 60)
    for entry in sorted(result.stats, key=lambda item: item.name):
        print(
            f"  {entry.name}\n"
            f"    count={entry.requests}  fail={entry.failures}"
            f"  avg={entry.avg_ms:.2f}ms"
            f"  p50={entry.percentile(0.50):.2f}ms"
            f"  p95={entry.percentile(0.95):.2f}ms"
            f"  min={entry.min_ms if entry.requests else 0.0:.2f}ms"
            f"  max={entry.max_ms:.2f}ms"
        )
        if entry.status_codes:
            print(f"    status_codes={entry.status_codes}")
        if entry.last_error:
            print(f"    last_error={entry.last_error}")

    checks = getattr(result, "checks", [])
    if checks:
        print("\nCheckpoints")
        print("-" * 60)
        for c in checks:
            total = c.passes + c.failures
            rate = f"{c.passes / total:.0%}" if total else "n/a"
            status = "PASS" if c.failures == 0 else "FAIL"
            print(
                f"  [{status}] {c.name}  pass={c.passes}  fail={c.failures}  rate={rate}"
            )
            if c.last_message:
                print(f"         msg={c.last_message}")

    if result.user_errors:
        print("\nUser Errors")
        print("-" * 60)
        for error in result.user_errors:
            print(f"  {error}")

    print("=" * 60)
