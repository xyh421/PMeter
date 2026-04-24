from __future__ import annotations

import importlib.util
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from pmeter.dsl import User
from pmeter.http import HttpClient
from pmeter.stats import CheckEntry, StatsCollector


@dataclass(slots=True)
class Environment:
    host: str | None
    stats: StatsCollector
    stop_event: threading.Event


class HttpUser(User):
    abstract = True
    host: str | None = None

    def __init__(self, environment: Environment):
        super().__init__(environment)
        resolved_host = environment.host or self.host

        # Collect @pre_processor / @post_processor methods
        pre: list = []
        post: list = []
        for name in dir(self):
            method = getattr(self, name, None)
            if callable(method):
                if getattr(method, "_pmeter_pre_processor", False):
                    pre.append(method)
                if getattr(method, "_pmeter_post_processor", False):
                    post.append(method)

        self.client = HttpClient(environment, resolved_host, pre_processors=pre, post_processors=post)

        # Shared variable bag for correlation / extraction
        self.vars: dict[str, Any] = {}

    def check(self, name: str, condition: bool, message: str = "") -> bool:
        """Record a named checkpoint result (自定义检查点).

        Returns *condition* so it can be used in an ``assert`` or ``if``.
        """
        self.environment.stats.record_check(name, passed=condition, message=message)
        return condition

    def on_stop(self) -> None:
        self.client.close()


@dataclass
class RunResult:
    stats: list
    totals: dict
    user_errors: list[str]
    duration_seconds: float
    checks: list[CheckEntry] = field(default_factory=list)


def load_module(scene_file: str | Path) -> ModuleType:
    path = Path(scene_file).resolve()
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load scene file: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def discover_users(module: ModuleType) -> list[type[User]]:
    user_classes: list[type[User]] = []
    for value in module.__dict__.values():
        if (
            isinstance(value, type)
            and issubclass(value, User)
            and value is not User
            and not value.__dict__.get("abstract", False)
        ):
            user_classes.append(value)
    if not user_classes:
        raise RuntimeError("no concrete User classes found in the scene file")
    return user_classes


def run(
    scene_file: str | Path,
    *,
    users: int,
    spawn_rate: float,
    run_time: float,
    host: str | None = None,
    web_ui_port: int | None = None,
) -> RunResult:
    if users <= 0:
        raise ValueError("users must be greater than 0")
    if spawn_rate <= 0:
        raise ValueError("spawn_rate must be greater than 0")
    if run_time <= 0:
        raise ValueError("run_time must be greater than 0")

    module = load_module(scene_file)
    user_classes = discover_users(module)
    stats = StatsCollector()
    stop_event = threading.Event()
    environment = Environment(host=host, stats=stats, stop_event=stop_event)
    user_errors: list[str] = []
    error_lock = threading.Lock()
    threads: list[threading.Thread] = []
    started = time.perf_counter()

    # Optional live Web UI (Web UI)
    web_ui = None
    if web_ui_port is not None:
        from pmeter.web_ui import WebUIServer
        web_ui = WebUIServer(environment, port=web_ui_port)
        web_ui.start()

    def record_user_error(message: str) -> None:
        with error_lock:
            user_errors.append(message)

    def user_loop(user_class: type[User]) -> None:
        user = user_class(environment)
        try:
            user.on_start()
            while not stop_event.is_set():
                user.run_next_task()
                if stop_event.is_set():
                    break
                user.sleep()
        except Exception as exc:
            record_user_error(f"{user_class.__name__}: {exc}")
        finally:
            user.on_stop()

    class_pool = _build_class_pool(user_classes)
    spawn_interval = 1 / spawn_rate
    for _ in range(users):
        user_class = random.choice(class_pool)
        thread = threading.Thread(target=user_loop, args=(user_class,), daemon=True)
        threads.append(thread)
        thread.start()
        time.sleep(spawn_interval)

    deadline = time.perf_counter() + run_time
    try:
        while time.perf_counter() < deadline:
            time.sleep(0.2)
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=5)

    duration_seconds = time.perf_counter() - started
    result = RunResult(
        stats=stats.snapshot(),
        totals=stats.totals(),
        user_errors=user_errors,
        duration_seconds=duration_seconds,
        checks=stats.check_snapshot(),
    )

    if web_ui is not None:
        # Keep web UI alive briefly so the final state is visible in the browser
        import time as _time
        print("Test complete. Web UI still available — press Ctrl+C to exit.")
        try:
            while True:
                _time.sleep(1)
        except KeyboardInterrupt:
            pass
        web_ui.stop()

    return result


def _build_class_pool(user_classes: list[type[User]]) -> list[type[User]]:
    pool: list[type[User]] = []
    for user_class in user_classes:
        weight = getattr(user_class, "weight", 1)
        if weight <= 0:
            continue
        pool.extend([user_class] * weight)
    if not pool:
        raise RuntimeError("all user class weights are invalid")
    return pool
