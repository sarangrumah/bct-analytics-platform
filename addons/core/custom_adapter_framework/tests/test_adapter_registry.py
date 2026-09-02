# -*- coding: utf-8 -*-
from __future__ import annotations

import time

from odoo.tests import TransactionCase, tagged

from ..models.adapter_base import BaseAdapter, AdapterResponse, CircuitBreakerOpenError
from ..models.adapter_registry import register_adapter, get_adapter_class


@register_adapter("custom_test_mock")
class _MockAdapter(BaseAdapter):
    fail_until = 0
    call_count = 0
    raise_network = False

    def call(self, endpoint, payload=None, timeout=None, method="POST", extra_headers=None):
        self._cb_precheck()
        cls = type(self)
        cls.call_count += 1
        if cls.raise_network:
            self._cb_record_failure()
            return AdapterResponse(ok=False, status_code=0, error="network down")
        if cls.call_count <= cls.fail_until:
            self._cb_record_failure()
            return AdapterResponse(ok=False, status_code=503, error="boom")
        self._cb_record_success()
        return AdapterResponse(ok=True, status_code=200, data={"ping": "pong"})


@tagged("post_install", "-at_install")
class TestAdapterRegistry(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["custom.adapter.config"]

    def _reset_mock(self):
        _MockAdapter.fail_until = 0
        _MockAdapter.call_count = 0
        _MockAdapter.raise_network = False

    def _make_config(self, **overrides):
        vals = {
            "name": overrides.pop("name", f"mock-{time.time_ns()}"),
            "adapter_type": "custom_test_mock",
            "base_url": "http://localhost/none",
            "auth_method": "none",
            "timeout_s": 1,
            "retry_count": 1,
            "circuit_breaker_threshold": 3,
            "circuit_breaker_cooldown_s": 60,
        }
        vals.update(overrides)
        return self.Config.create(vals)

    def test_registry_resolves_class(self):
        self._reset_mock()
        self.assertIs(get_adapter_class("custom_test_mock"), _MockAdapter)
        cfg = self._make_config()
        adapter = cfg.get_adapter()
        self.assertIsInstance(adapter, _MockAdapter)
        resp = adapter.call("ping")
        self.assertTrue(resp.ok)
        self.assertEqual(resp.data, {"ping": "pong"})

    def test_circuit_breaker_opens_after_threshold(self):
        self._reset_mock()
        cfg = self._make_config(circuit_breaker_threshold=3)
        adapter = cfg.get_adapter()
        _MockAdapter.raise_network = True
        for _ in range(3):
            r = adapter.call("ping")
            self.assertFalse(r.ok)
        cfg.invalidate_recordset()
        self.assertEqual(cfg.status, "circuit_open")
        self.assertGreaterEqual(cfg.consecutive_failures, 3)
        # Next call must short-circuit.
        with self.assertRaises(CircuitBreakerOpenError):
            adapter.call("ping")

    def test_circuit_breaker_half_open_recovers_on_success(self):
        self._reset_mock()
        cfg = self._make_config(circuit_breaker_threshold=2, circuit_breaker_cooldown_s=0)
        adapter = cfg.get_adapter()
        _MockAdapter.raise_network = True
        for _ in range(2):
            adapter.call("ping")
        cfg.invalidate_recordset()
        self.assertEqual(cfg.status, "circuit_open")
        # Cooldown is 0 so probe is allowed; flip to success.
        _MockAdapter.raise_network = False
        _MockAdapter.fail_until = 0
        resp = adapter.call("ping")
        self.assertTrue(resp.ok)
        cfg.invalidate_recordset()
        self.assertEqual(cfg.status, "active")
        self.assertEqual(cfg.consecutive_failures, 0)

    def test_call_log_appended(self):
        self._reset_mock()
        cfg = self._make_config()
        before = self.env["custom.adapter.call.log"].search_count([("config_id", "=", cfg.id)])
        cfg.get_adapter().call("ping", payload={"a": 1})
        after = self.env["custom.adapter.call.log"].search_count([("config_id", "=", cfg.id)])
        self.assertEqual(after, before + 1)
