from __future__ import annotations

import asyncio
import inspect
import random
from typing import Callable


TaskCallable = Callable[["User"], None]
WaitTimeCallable = Callable[[], float]


def task(weight: int = 1):
    if weight <= 0:
        raise ValueError("task weight must be greater than 0")

    def decorator(func: TaskCallable) -> TaskCallable:
        setattr(func, "_pmeter_task_weight", weight)
        return func

    return decorator


def between(min_seconds: float, max_seconds: float) -> WaitTimeCallable:
    if min_seconds < 0 or max_seconds < 0:
        raise ValueError("wait time must be non-negative")
    if min_seconds > max_seconds:
        raise ValueError("min_seconds cannot be greater than max_seconds")

    def wait_time(*_args, **_kwargs) -> float:
        return random.uniform(min_seconds, max_seconds)

    return wait_time


def constant(seconds: float) -> WaitTimeCallable:
    if seconds < 0:
        raise ValueError("wait time must be non-negative")

    def wait_time(*_args, **_kwargs) -> float:
        return seconds

    return wait_time


class User:
    abstract = True
    wait_time: WaitTimeCallable = constant(0)
    weight = 1

    def __init__(self, environment):
        self.environment = environment
        self._task_cache = self._collect_tasks()

    def _collect_tasks(self) -> list[TaskCallable]:
        weighted_tasks: list[TaskCallable] = []
        for name in dir(self):
            candidate = getattr(self, name)
            weight = getattr(candidate, "_pmeter_task_weight", 0)
            if weight > 0 and callable(candidate):
                weighted_tasks.extend([candidate] * weight)
        return weighted_tasks

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass

    async def sleep(self) -> None:
        await asyncio.sleep(self.wait_time())

    async def run_next_task(self) -> None:
        if not self._task_cache:
            raise RuntimeError(f"{self.__class__.__name__} has no @task methods")
        fn = random.choice(self._task_cache)
        if inspect.iscoroutinefunction(fn):
            await fn()
        else:
            fn()
