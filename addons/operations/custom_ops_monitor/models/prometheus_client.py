# -*- coding: utf-8 -*-
"""Thin wrapper around the Prometheus HTTP query API.

Intentionally not an Odoo model — instantiated on demand inside cron
handlers. Uses ``urllib.request`` so the addon has zero extra Python
dependencies beyond the Odoo base. If ``requests`` is vendored elsewhere
that's fine, but we don't require it here.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

_logger = logging.getLogger(__name__)


class PrometheusError(RuntimeError):
    pass


class PrometheusClient:
    DEFAULT_TIMEOUT_S = 5

    def __init__(self, base_url: str, timeout_s: int = DEFAULT_TIMEOUT_S):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_s = timeout_s

    @classmethod
    def from_env(cls, env) -> "PrometheusClient":
        url = (
            env["ir.config_parameter"]
            .sudo()
            .get_param(
                "custom_ops_monitor.prometheus_url",
                "http://prometheus:9090",
            )
        )
        timeout = int(
            env["ir.config_parameter"]
            .sudo()
            .get_param(
                "custom_ops_monitor.prometheus_timeout_s",
                "5",
            )
            or 5
        )
        return cls(url, timeout_s=timeout)

    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict) -> dict:
        if not self.base_url:
            raise PrometheusError("Prometheus URL not configured.")
        # urlopen honours file://, ftp:// and data:// as well as http(s). base_url is
        # operator-configured rather than attacker-supplied, but a mistyped or
        # migrated parameter naming a local path would be read off disk and parsed
        # as a Prometheus response. The scheme check removes that class outright.
        if not self.base_url.startswith(("http://", "https://")):
            raise PrometheusError("Prometheus URL must be http:// or https://.")
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310 - scheme checked above
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
                payload = resp.read()
        except urllib.error.URLError as e:
            raise PrometheusError(f"prometheus unreachable: {e}") from e
        try:
            data = json.loads(payload.decode("utf-8"))
        except ValueError as e:
            raise PrometheusError(f"non-json response: {e}") from e
        if data.get("status") != "success":
            raise PrometheusError(f"prometheus error: {data.get('errorType')}: {data.get('error')}")
        return data.get("data", {})

    # ------------------------------------------------------------------

    def query(self, promql: str) -> list[dict]:
        """Instant query. Returns the ``result`` list (each entry has
        ``metric`` dict and ``value`` ``[ts, value_str]``)."""
        data = self._get("/api/v1/query", {"query": promql})
        return data.get("result", []) or []

    def query_range(
        self,
        promql: str,
        start: float | datetime,
        end: float | datetime,
        step: str = "60s",
    ) -> list[dict]:
        s = start.timestamp() if isinstance(start, datetime) else float(start)
        e = end.timestamp() if isinstance(end, datetime) else float(end)
        data = self._get(
            "/api/v1/query_range",
            {
                "query": promql,
                "start": f"{s:.0f}",
                "end": f"{e:.0f}",
                "step": step,
            },
        )
        return data.get("result", []) or []

    # ------------------------------------------------------------------

    @staticmethod
    def first_value(result: list[dict], default: float = 0.0) -> float:
        if not result:
            return default
        v = result[0].get("value") or [0, default]
        try:
            return float(v[1])
        except (TypeError, ValueError, IndexError):
            return default

    @staticmethod
    def values_by_label(result: list[dict], label: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for entry in result:
            lbl = (entry.get("metric") or {}).get(label)
            if not lbl:
                continue
            try:
                out[lbl] = float((entry.get("value") or [0, 0])[1])
            except (TypeError, ValueError, IndexError):
                continue
        return out
