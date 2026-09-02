# -*- coding: utf-8 -*-
"""Test harness for the ESB connector.

There are no ESB credentials, so every test runs against ``MockEsbTransport``:
a stand-in for ``requests.request`` that answers from JSON fixtures transcribed
verbatim from the ESB documentation's own ``Success-Response`` examples. Keeping
the fixtures literal is the point — if ESB's envelope changes, the fixtures are
what we re-capture and diff.

Routes are matched by ``(METHOD, path-substring)`` in registration order, so a
test can register a narrow route before a broad one to override it.
"""

from __future__ import annotations

import json
import os

from odoo.tests.common import TransactionCase

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name: str) -> dict:
    with open(os.path.join(FIXTURE_DIR, f"{name}.json"), encoding="utf-8") as fh:
        return json.load(fh)


class FakeResponse:
    """Minimal duck-type of ``requests.Response`` for BaseAdapter."""

    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = json.dumps(payload) if payload is not None else ""
        self.content = self.text.encode("utf-8")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class MockEsbTransport:
    """Records calls and replays fixtures. Install with ``patch_requests``."""

    def __init__(self):
        self.routes = []
        self.calls = []

    def register(self, method, path_fragment, payload, status_code=200, headers=None, times=None):
        """Answer ``method`` requests whose URL contains ``path_fragment``.

        ``payload`` may be a dict, or a callable ``(url, body) -> dict`` for
        responses that depend on what was sent. ``times`` limits how often the
        route answers, so a test can script a sequence (fail once, then succeed).
        """
        self.routes.append(
            {
                "method": method.upper(),
                "fragment": path_fragment,
                "payload": payload,
                "status_code": status_code,
                "headers": headers or {},
                "remaining": times,
            }
        )
        return self

    def __call__(self, method, url, data=None, headers=None, timeout=None, **kw):
        body = None
        if data:
            try:
                body = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
            except (ValueError, AttributeError):
                body = data
        self.calls.append({"method": method.upper(), "url": url, "body": body, "headers": dict(headers or {})})
        for route in self.routes:
            if route["method"] != method.upper() or route["fragment"] not in url:
                continue
            if route["remaining"] is not None:
                if route["remaining"] <= 0:
                    continue
                route["remaining"] -= 1
            payload = route["payload"]
            if callable(payload):
                payload = payload(url, body)
            return FakeResponse(payload, route["status_code"], route["headers"])
        raise AssertionError(f"MockEsbTransport: no route registered for {method.upper()} {url}")

    # -- assertions ---------------------------------------------------

    def calls_to(self, method, fragment):
        return [c for c in self.calls if c["method"] == method.upper() and fragment in c["url"]]

    def count(self, method, fragment):
        return len(self.calls_to(method, fragment))


class EsbTestCase(TransactionCase):
    """Base case: three adapter configs, a session, and a patched transport."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.param = cls.env["ir.config_parameter"].sudo()
        cls.param.set_param("custom_esb_connector.esb_password", "s3cr3t")

        cls.core_config = cls.env.ref("custom_esb_connector.adapter_esb_core")
        cls.corev1_config = cls.env.ref("custom_esb_connector.adapter_esb_corev1")
        cls.core_config.write({"status": "active", "retry_count": 1, "base_url": "https://esb.test/core"})
        cls.corev1_config.write({"status": "active", "retry_count": 1, "base_url": "https://esb.test/v1"})

        cls.session = cls.env.ref("custom_esb_connector.session_esb_core")
        cls.session.write({"username": "ODOOINT", "credential_ref": "custom_esb_connector.esb_password"})

    def setUp(self):
        super().setUp()
        self.transport = MockEsbTransport()
        patcher = self._patch_requests(self.transport)
        self.addCleanup(patcher)

    @staticmethod
    def _patch_requests(transport):
        """Swap ``requests.request`` inside the adapter module; return an undo."""
        from odoo.addons.custom_adapter_framework.models import adapter_base

        original = adapter_base.requests.request
        adapter_base.requests.request = transport

        def undo():
            adapter_base.requests.request = original

        return undo

    # -- helpers ------------------------------------------------------

    def given_logged_in(self):
        """Register the login route and prime a valid token."""
        self.transport.register("POST", "/auth/login", load_fixture("login_ok"))
        return self.session._ensure_token()

    def set_flag(self, key, value="1"):
        self.param.set_param(key, value)

    def adapter(self, config=None):
        return (config or self.core_config).get_adapter()
