from __future__ import annotations

from typing import Callable


def pre_processor(func: Callable) -> Callable:
    """Mark a method as a pre-processor, called before every HTTP request.

    The method receives ``(method, url, kwargs)`` and may return a modified
    *kwargs* dict.  Returning ``None`` leaves *kwargs* unchanged.

    Example::

        class MyUser(HttpUser):
            @pre_processor
            def add_auth(self, method, url, kwargs):
                kwargs.setdefault("headers", {})["X-Token"] = self.token
                return kwargs
    """
    func._pmeter_pre_processor = True
    return func


def post_processor(func: Callable) -> Callable:
    """Mark a method as a post-processor, called after every HTTP request.

    The method receives the ``HttpResponse`` object.

    Example::

        class MyUser(HttpUser):
            @post_processor
            def log_slow(self, response):
                if response.elapsed_ms > 1000:
                    print(f"Slow request: {response.request_name}")
    """
    func._pmeter_post_processor = True
    return func
