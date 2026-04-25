"""
Feature: 前置/后置处理器 (@pre_processor / @post_processor)

验证装饰器标记、调用时机、kwargs 修改、多处理器顺序，
以及在完整场景运行中的正确行为。
"""
from __future__ import annotations

import textwrap

import pytest

from conftest import arun
from pmeter.dsl import task
from pmeter.processors import post_processor, pre_processor
from pmeter.runner import HttpUser, run


class TestDecoratorMarkers:
    """装饰器应正确在函数上打标记。"""

    def test_pre_processor_sets_flag(self):
        @pre_processor
        def fn(self, method, url, kwargs):
            pass
        assert getattr(fn, "_pmeter_pre_processor", False) is True

    def test_post_processor_sets_flag(self):
        @post_processor
        def fn(self, response):
            pass
        assert getattr(fn, "_pmeter_post_processor", False) is True

    def test_pre_does_not_set_post_flag(self):
        @pre_processor
        def fn(self, m, u, k): pass
        assert getattr(fn, "_pmeter_post_processor", False) is False

    def test_post_does_not_set_pre_flag(self):
        @post_processor
        def fn(self, r): pass
        assert getattr(fn, "_pmeter_pre_processor", False) is False

    def test_decorator_preserves_function_name(self):
        @pre_processor
        def my_processor(self, m, u, k): pass
        assert my_processor.__name__ == "my_processor"


class TestPreProcessorBehavior:
    """前置处理器在请求发出前被调用，可修改 kwargs。"""

    def test_pre_processor_collected_by_user(self, env):
        class MyUser(HttpUser):
            host = env.host

            @pre_processor
            def inject(self, method, url, kwargs):
                return kwargs

            @task(1)
            async def do(self): pass

        user = MyUser(env)
        assert len(user.client._pre) == 1

    def test_pre_processor_receives_method_and_url(self, env):
        calls: list[tuple] = []

        class MyUser(HttpUser):
            host = env.host

            @pre_processor
            def capture(self, method, url, kwargs):
                calls.append((method, url))
                return kwargs

            @task(1)
            async def do(self): pass

        user = MyUser(env)
        arun(user.client.get("/status/200"))
        assert calls == [("GET", "/status/200")]

    def test_pre_processor_can_add_header(self, env):
        added: list[str] = []

        class MyUser(HttpUser):
            host = env.host

            @pre_processor
            def inject(self, method, url, kwargs):
                kwargs.setdefault("headers", {})["X-PMeter"] = "yes"
                added.append("injected")
                return kwargs

            @task(1)
            async def do(self): pass

        user = MyUser(env)
        arun(user.client.get("/status/200"))
        assert "injected" in added

    def test_pre_processor_non_dict_return_ignored(self, env):
        """Returning None from a pre_processor is safe."""
        class MyUser(HttpUser):
            host = env.host

            @pre_processor
            def noop(self, method, url, kwargs):
                pass   # returns None implicitly

            @task(1)
            async def do(self): pass

        user = MyUser(env)
        resp = arun(user.client.get("/status/200"))
        assert resp.status_code == 200


class TestPostProcessorBehavior:
    """后置处理器在请求完成后被调用，接收 HttpResponse。"""

    def test_post_processor_receives_response(self, env):
        received = []

        class MyUser(HttpUser):
            host = env.host

            @post_processor
            def capture(self, response):
                received.append(response.status_code)

            @task(1)
            async def do(self): pass

        user = MyUser(env)
        arun(user.client.get("/status/200"))
        assert received == [200]

    def test_post_processor_sees_elapsed_ms(self, env):
        timings: list[float] = []

        class MyUser(HttpUser):
            host = env.host

            @post_processor
            def record(self, response):
                timings.append(response.elapsed_ms)

            @task(1)
            async def do(self): pass

        user = MyUser(env)
        arun(user.client.get("/slow"))
        assert len(timings) == 1
        assert timings[0] > 0


class TestMultipleProcessors:
    """多个处理器全部被调用，顺序稳定。"""

    def test_all_pre_processors_called(self, env):
        order: list[str] = []

        class MyUser(HttpUser):
            host = env.host

            @pre_processor
            def pre1(self, m, u, k):
                order.append("pre1")
                return k

            @pre_processor
            def pre2(self, m, u, k):
                order.append("pre2")
                return k

            @task(1)
            async def do(self): pass

        user = MyUser(env)
        arun(user.client.get("/status/200"))
        assert "pre1" in order and "pre2" in order

    def test_pre_and_post_both_called(self, env):
        order: list[str] = []

        class MyUser(HttpUser):
            host = env.host

            @pre_processor
            def pre(self, m, u, k):
                order.append("pre")
                return k

            @post_processor
            def post(self, r):
                order.append("post")

            @task(1)
            async def do(self): pass

        user = MyUser(env)
        arun(user.client.get("/status/200"))
        assert order.index("pre") < order.index("post")


class TestProcessorsInScene:
    """处理器在完整 run() 中生效。"""

    def test_pre_processor_in_full_run(self, mock_server, tmp_path):
        scene = tmp_path / "scene.py"
        scene.write_text(textwrap.dedent(f"""\
            from pmeter import HttpUser, task, constant, pre_processor

            class P(HttpUser):
                host = "{mock_server}"
                wait_time = constant(0)

                @pre_processor
                def add_header(self, method, url, kwargs):
                    kwargs.setdefault("headers", {{}})["X-Run"] = "1"
                    return kwargs

                @task(1)
                async def do(self):
                    await self.client.get("/status/200")
        """))
        result = run(scene, users=2, spawn_rate=2.0, run_time=1.5)
        assert result.totals["requests"] > 0
        assert result.user_errors == []
