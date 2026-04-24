from __future__ import annotations

import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urljoin

import requests

from pmeter.stats import StatsCollector


class HttpAssertionError(AssertionError):
    pass


@dataclass(slots=True)
class HttpResponse:
    raw: requests.Response
    elapsed_ms: float
    request_name: str
    stats: StatsCollector
    _assertion_failed: bool = False

    @property
    def status_code(self) -> int:
        return self.raw.status_code

    @property
    def text(self) -> str:
        return self.raw.text

    @property
    def headers(self) -> requests.structures.CaseInsensitiveDict[str]:
        return self.raw.headers

    def json(self) -> Any:
        return self.raw.json()

    # ---- Assertions ----

    def assert_status(self, expected: int) -> "HttpResponse":
        if self.status_code != expected:
            error = HttpAssertionError(
                f"expected status {expected}, got {self.status_code}"
            )
            self._mark_assertion_failure(error)
            raise error
        return self

    def assert_body_contains(self, expected: str) -> "HttpResponse":
        if expected not in self.text:
            error = HttpAssertionError(f"response body does not contain {expected!r}")
            self._mark_assertion_failure(error)
            raise error
        return self

    def assert_json_path(self, path: str, expected: Any) -> "HttpResponse":
        actual = self._read_json_path(path)
        if actual != expected:
            error = HttpAssertionError(
                f"expected json path {path!r} to be {expected!r}, got {actual!r}"
            )
            self._mark_assertion_failure(error)
            raise error
        return self

    # ---- Extractors (关联提取) ----

    def extract_regex(self, pattern: str, group: int = 1) -> str | None:
        """Extract a value from the response body using a regex.

        Returns ``None`` if the pattern does not match.
        """
        match = re.search(pattern, self.text)
        return match.group(group) if match else None

    def extract_json_path(self, path: str) -> Any:
        """Extract a value from the JSON body using dot-notation path."""
        return self._read_json_path(path)

    def extract_header(self, name: str) -> str | None:
        """Return the value of a response header, or ``None``."""
        return self.headers.get(name)

    def extract_cookie(self, name: str) -> str | None:
        """Return the value of a response cookie, or ``None``."""
        return self.raw.cookies.get(name)

    # ---- Internal ----

    def _read_json_path(self, path: str) -> Any:
        current: Any = self.json()
        for segment in path.split("."):
            if isinstance(current, list):
                current = current[int(segment)]
            else:
                current = current[segment]
        return current

    def _mark_assertion_failure(self, error: Exception) -> None:
        if self._assertion_failed:
            return
        self._assertion_failed = True
        self.stats.reclassify_success_as_failure(
            self.request_name,
            self.elapsed_ms,
            self.status_code,
            error,
        )


class HttpClient:
    def __init__(
        self,
        environment,
        host: str | None,
        pre_processors: list[Callable] | None = None,
        post_processors: list[Callable] | None = None,
    ):
        self.environment = environment
        self.host = host
        self.session = requests.Session()
        self._pre: list[Callable] = pre_processors or []
        self._post: list[Callable] = post_processors or []

    def close(self) -> None:
        with suppress(Exception):
            self.session.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        name: str | None = None,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> HttpResponse:
        target = self._build_url(url)
        request_name = name or f"{method.upper()} {url}"

        # Pre-processors
        for fn in self._pre:
            result = fn(method, url, kwargs)
            if isinstance(result, dict):
                kwargs = result

        started = time.perf_counter()
        try:
            raw = self.session.request(method=method, url=target, timeout=timeout, **kwargs)
            elapsed_ms = (time.perf_counter() - started) * 1000
            response = HttpResponse(
                raw=raw,
                elapsed_ms=elapsed_ms,
                request_name=request_name,
                stats=self.environment.stats,
            )
            self.environment.stats.record_success(request_name, elapsed_ms, raw.status_code)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.environment.stats.record_failure(request_name, elapsed_ms, exc)
            raise

        # Post-processors
        for fn in self._post:
            fn(response)

        return response

    def get(self, url: str, **kwargs: Any) -> HttpResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> HttpResponse:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> HttpResponse:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> HttpResponse:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> HttpResponse:
        return self.request("DELETE", url, **kwargs)

    def _build_url(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if not self.host:
            raise ValueError("relative URL requires a host, set host on the user or CLI")
        return urljoin(self.host.rstrip("/") + "/", url.lstrip("/"))
