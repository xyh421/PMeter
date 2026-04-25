from __future__ import annotations

import asyncio
import inspect
import re
from contextlib import suppress
from typing import Any, Callable
from urllib.parse import urljoin

import aiohttp

from pmeter.stats import StatsCollector


class HttpAssertionError(AssertionError):
    pass


class HttpResponse:
    """Wraps an aiohttp response with timing, assertions, and extractors."""

    def __init__(
        self,
        *,
        status_code: int,
        text: str,
        headers: dict[str, str],
        cookies: dict[str, str],
        elapsed_ms: float,
        request_name: str,
        stats: StatsCollector,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.elapsed_ms = elapsed_ms
        self.request_name = request_name
        self._headers = headers
        self._cookies = cookies
        self._stats = stats
        self._assertion_failed = False

    @property
    def headers(self) -> dict[str, str]:
        return self._headers

    def json(self) -> Any:
        import json
        return json.loads(self.text)

    # ---- Assertions (chainable) ----

    def assert_status(self, expected: int) -> "HttpResponse":
        if self.status_code != expected:
            err = HttpAssertionError(f"expected status {expected}, got {self.status_code}")
            self._mark_failure(err)
            raise err
        return self

    def assert_body_contains(self, expected: str) -> "HttpResponse":
        if expected not in self.text:
            err = HttpAssertionError(f"response body does not contain {expected!r}")
            self._mark_failure(err)
            raise err
        return self

    def assert_json_path(self, path: str, expected: Any) -> "HttpResponse":
        actual = self._read_json_path(path)
        if actual != expected:
            err = HttpAssertionError(
                f"expected json path {path!r} to be {expected!r}, got {actual!r}"
            )
            self._mark_failure(err)
            raise err
        return self

    # ---- Extractors (关联提取) ----

    def extract_regex(self, pattern: str, group: int = 1) -> str | None:
        m = re.search(pattern, self.text)
        return m.group(group) if m else None

    def extract_json_path(self, path: str) -> Any:
        return self._read_json_path(path)

    def extract_header(self, name: str) -> str | None:
        return self._headers.get(name.lower())

    def extract_cookie(self, name: str) -> str | None:
        return self._cookies.get(name)

    # ---- Internal ----

    def _read_json_path(self, path: str) -> Any:
        current: Any = self.json()
        for segment in path.split("."):
            if isinstance(current, list):
                current = current[int(segment)]
            else:
                current = current[segment]
        return current

    def _mark_failure(self, error: Exception) -> None:
        if self._assertion_failed:
            return
        self._assertion_failed = True
        self._stats.reclassify_success_as_failure(
            self.request_name, self.elapsed_ms, self.status_code, error
        )


class HttpClient:
    def __init__(
        self,
        environment,
        host: str | None,
        pre_processors: list[Callable] | None = None,
        post_processors: list[Callable] | None = None,
    ) -> None:
        self.environment = environment
        self.host = host
        self._pre: list[Callable] = pre_processors or []
        self._post: list[Callable] = post_processors or []
        self._session: aiohttp.ClientSession | None = None

    async def _session_(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def request(
        self,
        method: str,
        url: str,
        *,
        name: str | None = None,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> HttpResponse:
        import time
        target = self._build_url(url)
        request_name = name or f"{method.upper()} {url}"

        # Pre-processors
        for fn in self._pre:
            result = await fn(method, url, kwargs) if inspect.iscoroutinefunction(fn) else fn(method, url, kwargs)
            if isinstance(result, dict):
                kwargs = result

        session = await self._session_()
        timeout_obj = aiohttp.ClientTimeout(total=timeout)

        started = time.perf_counter()
        try:
            async with session.request(method, target, timeout=timeout_obj, **kwargs) as raw:
                text = await raw.text()
                elapsed_ms = (time.perf_counter() - started) * 1000
                status_code = raw.status
                headers = {k.lower(): v for k, v in raw.headers.items()}
                cookies = {k: v.value for k, v in raw.cookies.items()}
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.environment.stats.record_failure(request_name, elapsed_ms, exc)
            raise

        self.environment.stats.record_success(request_name, elapsed_ms, status_code)
        response = HttpResponse(
            status_code=status_code,
            text=text,
            headers=headers,
            cookies=cookies,
            elapsed_ms=elapsed_ms,
            request_name=request_name,
            stats=self.environment.stats,
        )

        # Post-processors
        for fn in self._post:
            if inspect.iscoroutinefunction(fn):
                await fn(response)
            else:
                fn(response)

        return response

    async def get(self, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> HttpResponse:
        return await self.request("DELETE", url, **kwargs)

    def _build_url(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if not self.host:
            raise ValueError("relative URL requires a host, set host on the user or CLI")
        return urljoin(self.host.rstrip("/") + "/", url.lstrip("/"))
