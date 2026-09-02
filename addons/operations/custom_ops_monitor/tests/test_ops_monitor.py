# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import patch

from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.custom_ops_monitor.models.prometheus_client import (
    PrometheusClient,
)


def _fake_prom_result(label_db: str, value: float) -> list[dict]:
    return [{"metric": {"db": label_db}, "value": [0, str(value)]}]


@tagged("post_install", "-at_install")
class TestOpsMonitorCron(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Tenant = self.env["tenant.registry"]
        self.Health = self.env["custom.ops.tenant.health"]
        self.tenant = self.Tenant.create(
            {
                "slug": "test-tenant-1",
                "display_name": "Test Tenant",
                "db_name": "tenant_test1",
                "state": "active",
            }
        )

    def test_cron_collect_creates_snapshot(self):
        def _fake_query(self_, promql):  # noqa: D401
            # Simple deterministic mapping per query.
            if "cpu" in promql:
                return _fake_prom_result("tenant_test1", 42.0)
            if "memory_usage_ratio" in promql:
                return _fake_prom_result("tenant_test1", 71.0)
            if "memory_used_bytes" in promql:
                return _fake_prom_result("tenant_test1", 4096.0)
            if "memory_total_bytes" in promql:
                return _fake_prom_result("tenant_test1", 8192.0)
            if "node_filesystem_avail" in promql and "1 -" in promql:
                return _fake_prom_result("tenant_test1", 55.0)
            if "node_filesystem_size_bytes" in promql:
                return _fake_prom_result("tenant_test1", 100.0)
            if "odoo_http_requests_total" in promql and "rate" in promql and "60" in promql:
                return _fake_prom_result("tenant_test1", 12.0)
            if "5.." in promql:
                return _fake_prom_result("tenant_test1", 0.5)
            if "pg_database_size_bytes" in promql:
                return _fake_prom_result("tenant_test1", 250.0)
            if "redis_keyspace_hits" in promql:
                return _fake_prom_result("tenant_test1", 98.0)
            return []

        before = self.Health.search_count([("tenant_id", "=", self.tenant.id)])
        with patch.object(PrometheusClient, "query", _fake_query):
            self.Health._cron_collect_snapshots()
        after = self.Health.search_count([("tenant_id", "=", self.tenant.id)])
        self.assertEqual(after, before + 1)
        snap = self.Health.search(
            [("tenant_id", "=", self.tenant.id)],
            order="snapshot_at desc",
            limit=1,
        )
        self.assertEqual(snap.cpu_pct, 42.0)
        self.assertEqual(snap.memory_pct, 71.0)
        self.assertIn(snap.status, ("green", "yellow", "red"))
        self.assertGreaterEqual(snap.health_score, 0)
        self.assertLessEqual(snap.health_score, 100)

    def test_ingest_alertmanager_payload(self):
        Incident = self.env["custom.ops.incident"]
        payload = {
            "version": "4",
            "groupKey": '{}/{alertname="HighCPU"}',
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighCPU",
                        "severity": "critical",
                        "tenant": "test-tenant-1",
                    },
                    "annotations": {
                        "summary": "CPU above 90% for 5m",
                        "description": "Investigate process listing.",
                        "runbook_url": "https://example.com/rb/cpu",
                    },
                    "startsAt": "2026-05-19T10:00:00Z",
                    "endsAt": "0001-01-01T00:00:00Z",
                    "fingerprint": "abcdef1234567890",
                },
            ],
        }
        touched = Incident.ingest_alertmanager_payload(payload)
        self.assertEqual(len(touched), 1)
        inc = touched[0]
        self.assertEqual(inc.alert_name, "HighCPU")
        self.assertEqual(inc.severity, "critical")
        self.assertEqual(inc.tenant_id, self.tenant)
        self.assertEqual(inc.state, "firing")
        self.assertEqual(inc.fingerprint, "abcdef1234567890")

        # Same fingerprint with status=resolved updates the same incident.
        payload["alerts"][0]["status"] = "resolved"
        payload["alerts"][0]["endsAt"] = "2026-05-19T10:30:00Z"
        Incident.ingest_alertmanager_payload(payload)
        inc.invalidate_recordset()
        self.assertEqual(inc.state, "resolved")
        self.assertTrue(inc.resolved_at)


@tagged("post_install", "-at_install")
class TestAlertmanagerWebhook(HttpCase):
    def test_webhook_rejects_bad_json(self):
        # secure_endpoint will reject without HMAC; we still check at least
        # that the route exists and returns a 4xx (not a 404).
        resp = self.url_open(
            "/api/ops/alert",
            data="not-json",
            headers={"Content-Type": "application/json"},
        )
        self.assertIn(resp.status_code, (400, 401, 403))
