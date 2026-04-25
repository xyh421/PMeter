from __future__ import annotations

import asyncio
import importlib.util
import inspect
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

    def __init__(self, environment: Environment) -> None:
        super().__init__(environment)
        resolved_host = environment.host or self.host

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
        self.vars: dict[str, Any] = {}

    def check(self, name: str, condition: bool, message: str = "") -> bool:
        self.environment.stats.record_check(name, passed=condition, message=message)
        return condition

    async def on_stop(self) -> None:  # type: ignore[override]
        await self.client.close()


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


async def _async_run(
    scene_file: str | Path,
    *,
    users: int,
    spawn_rate: float,
    run_time: float,
    host: str | None = None,
    web_ui_port: int | None = None,
) -> RunResult:
    module = load_module(scene_file)
    user_classes = discover_users(module)
    stats = StatsCollector()
    stop_event = threading.Event()
    environment = Environment(host=host, stats=stats, stop_event=stop_event)
    user_errors: list[str] = []
    tasks: list[asyncio.Task] = []
    started = time.perf_counter()

    web_ui = None
    if web_ui_port is not None:
        from pmeter.web_ui import WebUIServer
        web_ui = WebUIServer(environment, port=web_ui_port)
        web_ui.start()

    def record_error(msg: str) -> None:
        user_errors.append(msg)

    async def user_loop(user_class: type[User]) -> None:
        user = user_class(environment)
        try:
            on_start = user.on_start
            if inspect.iscoroutinefunction(on_start):
                await on_start()
            else:
                on_start()

            while not stop_event.is_set():
                await user.run_next_task()
                if stop_event.is_set():
                    break
                await user.sleep()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            record_error(f"{user_class.__name__}: {exc}")
        finally:
            on_stop = user.on_stop
            if inspect.iscoroutinefunction(on_stop):
                await on_stop()
            else:
                on_stop()

    class_pool = _build_class_pool(user_classes)
    spawn_interval = 1.0 / spawn_rate

    async def spawner() -> None:
        for _ in range(users):
            if stop_event.is_set():
                break
            user_class = random.choice(class_pool)
            t = asyncio.create_task(user_loop(user_class))
            tasks.append(t)
            await asyncio.sleep(spawn_interval)

    asyncio.create_task(spawner())

    try:
        await asyncio.sleep(run_time)
    finally:
        stop_event.set()

    # Wait up to 10 s for in-flight requests to finish, then cancel remainder
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=10)
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    duration_seconds = time.perf_counter() - started
    result = RunResult(
        stats=stats.snapshot(),
        totals=stats.totals(),
        user_errors=user_errors,
        duration_seconds=duration_seconds,
        checks=stats.check_snapshot(),
    )

    if web_ui is not None:
        print("Test complete. Web UI still available — press Ctrl+C to exit.")
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        web_ui.stop()

    return result


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

    return asyncio.run(
        _async_run(
            scene_file,
            users=users,
            spawn_rate=spawn_rate,
            run_time=run_time,
            host=host,
            web_ui_port=web_ui_port,
        )
    )


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
