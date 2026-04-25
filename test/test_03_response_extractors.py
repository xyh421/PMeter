"""
Feature: 关联提取 (Response Extractors)

验证 extract_json_path / extract_regex / extract_header /
extract_cookie，以及通过 self.vars 跨请求共享提取值。
"""
from __future__ import annotations

import pytest

from conftest import arun
from pmeter.dsl import task
from pmeter.runner import HttpUser


class TestJsonPathExtractor:
    """extract_json_path — 按点号路径提取 JSON 值。"""

    def test_top_level_key(self, client):
        resp = arun(client.get("/token"))
        assert resp.extract_json_path("access_token") == "tok-xyz-789"

    def test_nested_key(self, client):
        resp = arun(client.get("/json"))
        assert resp.extract_json_path("slideshow.author") == "Yours Truly"

    def test_array_index(self, client):
        resp = arun(client.get("/json"))
        assert resp.extract_json_path("slideshow.items.0") == 1
        assert resp.extract_json_path("slideshow.items.1") == 2
        assert resp.extract_json_path("slideshow.items.2") == 3

    def test_missing_key_raises(self, client):
        resp = arun(client.get("/json"))
        with pytest.raises(KeyError):
            resp.extract_json_path("does_not_exist")

    def test_json_method_returns_dict(self, client):
        resp = arun(client.get("/json"))
        data = resp.json()
        assert isinstance(data, dict)
        assert "slideshow" in data


class TestRegexExtractor:
    """extract_regex — 从响应正文用正则提取捕获组。"""

    def test_match_returns_group(self, client):
        resp = arun(client.get("/json"))
        match = resp.extract_regex(r'"author":\s*"([^"]+)"')
        assert match == "Yours Truly"

    def test_no_match_returns_none(self, client):
        resp = arun(client.get("/json"))
        assert resp.extract_regex(r"NOPE(\d+)") is None

    def test_custom_group(self, client):
        resp = arun(client.get("/json"))
        # group=0 → entire match
        full = resp.extract_regex(r'"(author)"', group=0)
        assert full == '"author"'

    def test_multiple_calls_independent(self, client):
        resp = arun(client.get("/json"))
        a = resp.extract_regex(r'"author":\s*"([^"]+)"')
        b = resp.extract_regex(r'"items":\s*\[([^\]]+)\]')
        assert a == "Yours Truly"
        assert b is not None


class TestHeaderExtractor:
    """extract_header — 大小写不敏感地提取响应头。"""

    def test_content_type_present(self, client):
        resp = arun(client.get("/json"))
        ct = resp.extract_header("content-type")
        assert ct is not None and "json" in ct.lower()

    def test_case_insensitive(self, client):
        resp = arun(client.get("/json"))
        assert resp.extract_header("Content-Type") == resp.extract_header("content-type")

    def test_missing_header_returns_none(self, client):
        resp = arun(client.get("/json"))
        assert resp.extract_header("x-custom-nonexistent") is None

    def test_headers_property_is_dict(self, client):
        resp = arun(client.get("/status/200"))
        assert isinstance(resp.headers, dict)


class TestCookieExtractor:
    """extract_cookie — 从 Set-Cookie 响应头提取 cookie 值。"""

    def test_cookie_present(self, client):
        resp = arun(client.get("/set-cookie"))
        assert resp.extract_cookie("session") == "abc123"

    def test_missing_cookie_returns_none(self, client):
        resp = arun(client.get("/json"))
        assert resp.extract_cookie("nosuchcookie") is None

    def test_status_code_after_set_cookie(self, client):
        resp = arun(client.get("/set-cookie"))
        assert resp.status_code == 200


class TestCorrelation:
    """关联提取 — 通过 self.vars 在同一用户的请求之间传递提取值。"""

    def test_token_flows_to_next_request(self, env):
        class TokenUser(HttpUser):
            host = env.host

            @task(1)
            async def flow(self):
                resp = await self.client.get("/token")
                self.vars["token"] = resp.extract_json_path("access_token")
                # 用 token 请求受保护接口
                r2 = await self.client.get(
                    "/need-auth",
                    headers={"Authorization": f"Bearer {self.vars['token']}"},
                )
                assert r2.status_code == 200

        user = TokenUser(env)
        arun(user.flow())
        assert user.vars["token"] == "tok-xyz-789"

    def test_vars_persist_across_tasks(self, env):
        class VarUser(HttpUser):
            host = env.host

            @task(1)
            async def step1(self):
                resp = await self.client.get("/json")
                self.vars["author"] = resp.extract_json_path("slideshow.author")

            @task(1)
            async def step2(self):
                assert self.vars.get("author") == "Yours Truly"

        user = VarUser(env)
        arun(user.step1())
        arun(user.step2())

    def test_vars_start_empty(self, env):
        class EmptyVarUser(HttpUser):
            host = env.host

            @task(1)
            async def do(self):
                pass

        user = EmptyVarUser(env)
        assert user.vars == {}
