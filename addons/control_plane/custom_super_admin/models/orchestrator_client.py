# -*- coding: utf-8 -*-
"""HTTP client to tenant-orchestrator (HMAC-signed)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from odoo import api, models

_logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = os.environ.get("ORCHESTRATOR_URL", "http://tenant-orchestrator:8080")
DEFAULT_TIMEOUT = 180.0


class OrchestratorClient(models.AbstractModel):
    """Thin wrapper that signs every call to the orchestrator with HMAC.

    All methods raise ``UserError``-friendly exceptions on non-2xx; callers
    (mainly the action buttons on ``tenant.registry``) catch and surface
    them via Odoo notifications.
    """

    _name = "custom.super.admin.orchestrator.client"
    _description = "Orchestrator HTTP client"

    @api.model
    def _base_url(self) -> str:
        param = self.env["ir.config_parameter"].sudo().get_param("custom_super_admin.orchestrator_url")
        return (param or DEFAULT_BASE_URL).rstrip("/")

    @api.model
    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        url = f"{self._base_url()}{path}"
        body_bytes = json.dumps(body).encode() if body is not None else b""
        header, _ = self.env["custom.security"].sudo().sign_for("ORCHESTRATOR_SHARED_SECRET", body_bytes)
        actor_name = actor or (self.env.user.login if self.env.user else "system")
        headers = {
            "Content-Type": "application/json",
            "X-Custom-Signature": header,
            "X-Custom-Actor": actor_name,
        }
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            resp = client.request(method, url, content=body_bytes, headers=headers)
        if resp.status_code >= 400:
            _logger.warning(
                "orchestrator.error method=%s path=%s status=%s body=%s",
                method,
                path,
                resp.status_code,
                resp.text[:300],
            )
            raise RuntimeError(f"Orchestrator {method} {path} → {resp.status_code}: {resp.text[:200]}")
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    # ----- Tenant lifecycle -----

    @api.model
    def list_tenants(self, state: str | None = None) -> list[dict]:
        q = f"?state={state}" if state else ""
        return self._request("GET", f"/v1/tenants{q}")  # type: ignore[return-value]

    @api.model
    def get_tenant(self, slug: str) -> dict:
        return self._request("GET", f"/v1/tenants/{slug}")  # type: ignore[return-value]

    @api.model
    def provision(self, payload: dict) -> dict:
        return self._request("POST", "/v1/tenants", body=payload)  # type: ignore[return-value]

    @api.model
    def suspend(self, slug: str, reason: str | None = None) -> dict:
        return self._request("POST", f"/v1/tenants/{slug}/suspend", body={"reason": reason})  # type: ignore[return-value]

    @api.model
    def resume(self, slug: str) -> dict:
        return self._request("POST", f"/v1/tenants/{slug}/resume", body={})  # type: ignore[return-value]

    @api.model
    def archive(self, slug: str, retention_days: int = 30) -> dict:
        return self._request("DELETE", f"/v1/tenants/{slug}", body={"retention_days": retention_days})  # type: ignore[return-value]

    # ----- Backups -----

    @api.model
    def list_backups(self, slug: str, limit: int = 100) -> list[dict]:
        return self._request("GET", f"/v1/tenants/{slug}/backups?limit={limit}")  # type: ignore[return-value]

    @api.model
    def run_backup(self, slug: str, kind: str = "manual") -> dict:
        return self._request("POST", f"/v1/tenants/{slug}/backups", body={"kind": kind})  # type: ignore[return-value]

    @api.model
    def restore_backup(self, slug: str, s3_key: str, target_db: str | None = None) -> dict:
        return self._request(
            "POST",
            f"/v1/tenants/{slug}/backups/restore",
            body={"s3_key": s3_key, "target_db": target_db},
        )  # type: ignore[return-value]

    @api.model
    def replicate_backup(
        self,
        backup_id: int,
        target_tenant_slug: str,
        target_env: str = "staging",
    ) -> dict:
        return self._request(
            "POST",
            f"/v1/backups/{backup_id}/replicate",
            body={
                "target_tenant_slug": target_tenant_slug,
                "target_env": target_env,
            },
        )  # type: ignore[return-value]

    @api.model
    def enforce_backup_retention(self, slug: str, retention_days: int) -> dict:
        return self._request(
            "POST",
            "/v1/backups/enforce-retention",
            body={"tenant_slug": slug, "retention_days": retention_days},
        )  # type: ignore[return-value]

    @api.model
    def get_backup(self, backup_id: int) -> dict:
        return self._request("GET", f"/v1/backups/{backup_id}")  # type: ignore[return-value]
