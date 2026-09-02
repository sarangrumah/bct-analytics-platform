"""HTTP against the project's services, using only the standard library.

Non-2xx is a **result**, not an exception: most of what this suite asserts about the API is the
status code and body of a *rejection* (403 tenant scope violation, 401 on a tampered token).
"""

from __future__ import annotations

import dataclasses
import json
import urllib.error
import urllib.request

from .env import env


@dataclasses.dataclass(frozen=True)
class Response:
    status: int
    body: str
    headers: dict

    def json(self):
        return json.loads(self.body)


def request(url: str, method="GET", payload=None, headers=None, timeout=30) -> Response:
    # Scheme is checked before urllib sees the URL, not left to the default opener. urllib's
    # default handler set includes FileHandler and FTPHandler, so a `file:///etc/passwd` that
    # reached it would be *opened*, and a test helper that takes a URL is exactly the shape that
    # eventually gets one from somewhere else. `# noqa: S310` below is earned by this guard, not
    # waived past the rule.
    if not url.startswith(("http://127.0.0.1:", "http://localhost:", "http://", "https://")):
        raise ValueError("refusing a non-HTTP(S) URL: %r" % url)
    data = None
    hdrs = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return Response(resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers))
    except urllib.error.HTTPError as exc:  # a rejection is the thing under test
        return Response(exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers))


def gateway_url(path="") -> str:
    return f"http://127.0.0.1:{env('LOGIN_GATEWAY_HOST_PORT', '38120')}{path}"


def semantic_url(path="") -> str:
    return f"http://127.0.0.1:{env('SEMANTIC_API_HOST_PORT', '38200')}{path}"


def portal_url(path="") -> str:
    return f"http://127.0.0.1:{env('INSIGHT_PORTAL_HOST_PORT', '33000')}{path}"


def prometheus_url(path="") -> str:
    return f"http://127.0.0.1:{env('PROMETHEUS_HOST_PORT', '39090')}{path}"


def alertmanager_url(path="") -> str:
    return f"http://127.0.0.1:{env('ALERTMANAGER_HOST_PORT', '39093')}{path}"


def service_up(url: str) -> bool:
    try:
        return request(url, timeout=5).status < 500
    except OSError:
        return False
