# -*- coding: utf-8 -*-
"""``tenant.vps.deployer`` — thin wrapper that calls the orchestrator.

Reuses the existing HMAC-signed ``custom.super.admin.orchestrator.client``
``_request`` primitive so we don't duplicate the signing logic. Each method
is idempotent (the orchestrator side ensures that) and appends to
``vps.bootstrap_log`` so the OWL console can stream progress.
"""

from __future__ import annotations

import logging
from typing import Any

from odoo import api, models

_logger = logging.getLogger(__name__)


class VpsDeployer(models.AbstractModel):
    _name = "tenant.vps.deployer"
    _description = "Tenant VPS Deployer (orchestrator wrapper)"

    # ------------------------------------------------------------------

    def _orch(self):
        return self.env["custom.super.admin.orchestrator.client"].sudo()

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict:
        return self._orch()._request("POST", path, body=body or {})  # type: ignore[return-value]

    def _get(self, path: str) -> dict:
        return self._orch()._request("GET", path)  # type: ignore[return-value]

    @staticmethod
    def _vps_payload(vps) -> dict[str, Any]:
        # NB: credential REF only, never the secret material itself.
        return {
            "vps_id": vps.id,
            "hostname": vps.hostname,
            "public_ip": vps.public_ip or "",
            "ssh_port": vps.ssh_port,
            "ssh_user": vps.ssh_user,
            "ssh_credential_ref": vps.ssh_credential_ref,
        }

    # ------------------------------------------------------------------
    # Public API used by tenant.vps action_* methods
    # ------------------------------------------------------------------

    @api.model
    def register(self, vps) -> dict:
        return self._post("/v1/vps/register", self._vps_payload(vps))

    @api.model
    def bootstrap(self, vps) -> dict:
        return self._post(f"/v1/vps/{vps.id}/bootstrap", self._vps_payload(vps))

    @api.model
    def deploy_stack(self, vps, env) -> dict:
        body = {
            **self._vps_payload(vps),
            "env_type": env.env_type,
            "tenant_slug": env.tenant_registry_id.slug,
            "db_name": env.db_name,
        }
        return self._post(f"/v1/vps/{vps.id}/deploy-stack", body)

    @api.model
    def sync_addons(self, vps, env) -> dict:
        body = {
            **self._vps_payload(vps),
            "env_type": env.env_type,
            "tenant_slug": env.tenant_registry_id.slug,
            "db_name": env.db_name,
        }
        return self._post(f"/v1/vps/{vps.id}/sync-addons", body)

    @api.model
    def healthcheck(self, vps) -> dict:
        try:
            return self._get(f"/v1/vps/{vps.id}/health")
        except Exception as e:  # noqa: BLE001
            _logger.warning("vps healthcheck failed vps=%s err=%s", vps.id, e)
            return {"ok": False, "error": str(e)}

    @api.model
    def decommission(self, vps) -> dict:
        return self._post(f"/v1/vps/{vps.id}/decommission", self._vps_payload(vps))
