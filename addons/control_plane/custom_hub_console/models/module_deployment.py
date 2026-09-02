# -*- coding: utf-8 -*-
"""Module deployment: per-tenant install/upgrade/uninstall record.

Each row represents one orchestrator-mediated module operation on
exactly one tenant. The actual work is delegated to
``custom.super.admin.orchestrator.client`` — we only track requests,
state, and errors locally.

Track C extension: adds canary rollout, dependency resolution, pre-deploy
backup snapshot, healthcheck polling, and rollback handling. All extra
fields are optional so existing flows keep working when the canary path
is not used.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class CustomHubModuleDeployment(models.Model):
    _name = "custom.hub.module.deployment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Hub Module Deployment (per-tenant operation log)"
    _order = "requested_at desc, id desc"

    catalog_id = fields.Many2one(
        "custom.hub.module.catalog",
        string="Module",
        required=True,
        ondelete="restrict",
        index=True,
    )
    tenant_id = fields.Many2one(
        "tenant.registry",
        string="Tenant",
        required=True,
        ondelete="cascade",
        index=True,
    )
    deploy_mode = fields.Selection(
        [
            ("install", "Install"),
            ("upgrade", "Upgrade"),
            ("uninstall", "Uninstall"),
        ],
        required=True,
        default="install",
        index=True,
    )
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("installing", "Installing"),
            ("installed", "Installed"),
            ("upgrading", "Upgrading"),
            ("failed", "Failed"),
            ("uninstalled", "Uninstalled"),
        ],
        required=True,
        default="pending",
        index=True,
        tracking=True,
    )
    requested_by_id = fields.Many2one(
        "res.users",
        string="Requested By",
        default=lambda self: self.env.user.id,
        required=True,
    )
    requested_at = fields.Datetime(default=fields.Datetime.now, required=True)
    started_at = fields.Datetime()
    completed_at = fields.Datetime()
    error_message = fields.Text()

    # ------------------------------------------------------------------
    # Track C: canary & rollback
    # ------------------------------------------------------------------
    # ``environment_id`` (M2O to tenant.environment) is declared in
    # ``custom_tenant_infra/models/hub_module_deployment_extension.py``
    # since hub_console can't depend on tenant_infra (tenant_infra
    # depends on hub_console).
    dep_graph_resolved_json = fields.Text(
        string="Resolved Dependency Graph",
        help="JSON: {'order': [...], 'missing': [...]} produced by action_resolve_dependencies().",
    )
    canary_phase = fields.Selection(
        [
            ("none", "None"),
            ("canary", "Canary"),
            ("staged", "Staged Rollout"),
            ("full", "Full Rollout"),
            ("rolled_back", "Rolled Back"),
        ],
        default="none",
        index=True,
        tracking=True,
    )
    rollback_snapshot_id = fields.Many2one(
        "tenant.backup",
        string="Rollback Snapshot",
        ondelete="set null",
    )
    healthcheck_passed = fields.Boolean(string="Health OK")
    healthcheck_at = fields.Datetime(string="Last Healthcheck")

    # ------------------------------------------------------------------
    def action_deploy(self):
        """Best-effort: call orchestrator API; if unreachable, mark failed
        with a clear message — do not raise so wizard commits."""
        for rec in self:
            rec.started_at = fields.Datetime.now()
            rec.state = (
                "installing"
                if rec.deploy_mode == "install"
                else ("upgrading" if rec.deploy_mode == "upgrade" else "installing")
            )
            try:
                client = self.env["custom.super.admin.orchestrator.client"].sudo()
                slug = rec.tenant_id.slug
                module = rec.catalog_id.module_name
                # The orchestrator client doesn't yet have a generic
                # module-deploy endpoint helper; call ``_request`` directly.
                client._request(
                    "POST",
                    f"/v1/tenants/{slug}/modules/{rec.deploy_mode}",
                    body={"module": module},
                )
                rec.state = "uninstalled" if rec.deploy_mode == "uninstall" else "installed"
                rec.completed_at = fields.Datetime.now()
                rec.error_message = False
                self._log_audit(rec, "module_deploy", success=True)
            except Exception as exc:  # noqa: BLE001 - tolerant by design
                _logger.warning("[hub_deploy] orchestrator unreachable: %s", exc)
                rec.state = "failed"
                rec.completed_at = fields.Datetime.now()
                rec.error_message = f"Orchestrator API not reachable or returned error: {exc}"
                self._log_audit(rec, "module_deploy", success=False, error=str(exc))
        return True

    # ------------------------------------------------------------------
    # Track C action methods
    # ------------------------------------------------------------------
    def action_resolve_dependencies(self):
        """Topological sort of catalog deps for this deployment's module.

        Stores the result in ``dep_graph_resolved_json``::

            {"order": ["mod_a", "mod_b", ...], "missing": ["mod_x", ...]}

        ``missing`` lists module names referenced as deps but not present in
        the catalog.
        """
        Catalog = self.env["custom.hub.module.catalog"].sudo()
        for rec in self:
            order: list[str] = []
            missing: list[str] = []
            root = rec.catalog_id
            if root:
                # Shared deps-first toposort (also used by industry packs).
                order = Catalog._toposort_module_names(root)
                # Direct deps that aren't catalog rows themselves would be
                # flagged here; since depends_module_ids only holds catalog
                # rows this is normally empty (kept for forward-compat).
                referenced = {d.module_name for d in root.depends_module_ids}
                missing = sorted(referenced - set(order))
            payload = {"order": order, "missing": missing}
            rec.dep_graph_resolved_json = json.dumps(payload, sort_keys=True)
            self._log_audit(
                rec,
                "deps_resolved",
                success=True,
                extra={"order_len": len(order), "missing": missing},
            )
        return True

    def action_take_pre_backup(self):
        """Request a manual backup via orchestrator and link the snapshot.

        Looks up the newest ``tenant.backup`` mirror row for this tenant
        after the call. If the orchestrator is unreachable we mark the
        deployment failed but do not raise.
        """
        Backup = self.env["tenant.backup"].sudo()
        Client = self.env["custom.super.admin.orchestrator.client"].sudo()
        for rec in self:
            slug = rec.tenant_id.slug
            try:
                Client.run_backup(slug, kind="manual")
                # Refresh mirror so we can grab the freshly created row.
                try:
                    Backup._cron_sync_for(slug)
                except Exception as sync_exc:  # noqa: BLE001
                    _logger.debug("[hub_deploy] backup sync skipped: %s", sync_exc)
                snapshot = Backup.search(
                    [("tenant_slug", "=", slug)],
                    order="started_at desc, id desc",
                    limit=1,
                )
                if snapshot:
                    rec.rollback_snapshot_id = snapshot.id
                self._log_audit(
                    rec,
                    "pre_backup",
                    success=bool(snapshot),
                    extra={"snapshot_id": snapshot.id if snapshot else None},
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning("[hub_deploy] pre-backup failed: %s", exc)
                rec.error_message = f"Pre-deploy backup failed: {exc}"
                self._log_audit(
                    rec,
                    "pre_backup",
                    success=False,
                    error=str(exc),
                )
        return True

    def action_deploy_canary(self):
        """Deploy to the canary/staging environment for this tenant.

        Picks ``environment_id`` if set and its ``env_type == 'staging'``;
        otherwise tries to resolve a staging env from the optional
        ``tenant.environment`` model. Falls back to a generic deploy if no
        environment table exists.
        """
        for rec in self:
            target_env = rec._pick_canary_environment()
            try:
                client = self.env["custom.super.admin.orchestrator.client"].sudo()
                slug = rec.tenant_id.slug
                module = rec.catalog_id.module_name
                body = {"module": module, "phase": "canary"}
                if target_env and hasattr(target_env, "name"):
                    body["environment"] = target_env.name
                client._request(
                    "POST",
                    f"/v1/tenants/{slug}/modules/{rec.deploy_mode}",
                    body=body,
                )
                rec.canary_phase = "canary"
                rec.started_at = fields.Datetime.now()
                rec.state = "installing"
                self._log_audit(
                    rec,
                    "deploy_canary",
                    success=True,
                    extra={"env": target_env.name if target_env else None},
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning("[hub_deploy] canary deploy failed: %s", exc)
                rec.error_message = f"Canary deploy failed: {exc}"
                rec.state = "failed"
                self._log_audit(
                    rec,
                    "deploy_canary",
                    success=False,
                    error=str(exc),
                )
        return True

    def action_healthcheck(self):
        """Poll ``custom.ops.tenant.health`` and pass when green sustained.

        MVP: we look at the most recent health snapshot for the tenant. If
        it is green AND the snapshot was taken within the last 5 minutes,
        we accept it. The cron in ``custom_ops_monitor`` is expected to
        keep snapshots fresh. If no health table is available (module not
        installed) we conservatively mark not passed.
        """
        for rec in self:
            passed = False
            try:
                Health = self.env["custom.ops.tenant.health"].sudo()
                snap = Health.search(
                    [("tenant_id", "=", rec.tenant_id.id)],
                    order="snapshot_at desc",
                    limit=1,
                )
                if snap and snap.status == "green":
                    threshold = fields.Datetime.now() - timedelta(minutes=5)
                    if snap.snapshot_at and snap.snapshot_at >= threshold:
                        passed = True
            except KeyError:
                _logger.info("[hub_deploy] tenant.health model unavailable; skipping")
            rec.healthcheck_passed = passed
            rec.healthcheck_at = fields.Datetime.now()
            self._log_audit(
                rec,
                "healthcheck",
                success=passed,
                extra={"passed": passed},
            )
        return True

    def action_rollout_full(self):
        """Promote canary to full rollout. Gated on ``healthcheck_passed``."""
        for rec in self:
            if not rec.healthcheck_passed:
                rec.error_message = "Cannot promote to full rollout: healthcheck did not pass."
                self._log_audit(
                    rec,
                    "rollout_full",
                    success=False,
                    error="healthcheck_not_passed",
                )
                continue
            try:
                client = self.env["custom.super.admin.orchestrator.client"].sudo()
                slug = rec.tenant_id.slug
                module = rec.catalog_id.module_name
                client._request(
                    "POST",
                    f"/v1/tenants/{slug}/modules/{rec.deploy_mode}",
                    body={"module": module, "phase": "full"},
                )
                rec.canary_phase = "full"
                rec.state = "uninstalled" if rec.deploy_mode == "uninstall" else "installed"
                rec.completed_at = fields.Datetime.now()
                rec.error_message = False
                self._log_audit(rec, "rollout_full", success=True)
            except Exception as exc:  # noqa: BLE001
                _logger.warning("[hub_deploy] full rollout failed: %s", exc)
                rec.state = "failed"
                rec.error_message = f"Full rollout failed: {exc}"
                self._log_audit(
                    rec,
                    "rollout_full",
                    success=False,
                    error=str(exc),
                )
        return True

    def action_rollback(self):
        """Restore the linked snapshot via orchestrator (non-destructive)."""
        Client = self.env["custom.super.admin.orchestrator.client"].sudo()
        for rec in self:
            snapshot = rec.rollback_snapshot_id
            if not snapshot or not snapshot.s3_key:
                rec.error_message = "Cannot rollback: no snapshot linked or snapshot has no s3_key."
                self._log_audit(
                    rec,
                    "rollback",
                    success=False,
                    error="no_snapshot",
                )
                continue
            try:
                Client.restore_backup(
                    rec.tenant_id.slug,
                    snapshot.s3_key,
                    target_db=None,
                )
                rec.canary_phase = "rolled_back"
                rec.state = "failed"
                rec.completed_at = fields.Datetime.now()
                rec.error_message = rec.error_message or f"Rolled back to snapshot {snapshot.id} ({snapshot.s3_key})."
                self._log_audit(
                    rec,
                    "rollback",
                    success=True,
                    extra={"snapshot_id": snapshot.id},
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning("[hub_deploy] rollback failed: %s", exc)
                rec.error_message = f"Rollback failed: {exc}"
                self._log_audit(
                    rec,
                    "rollback",
                    success=False,
                    error=str(exc),
                )
        return True

    # ------------------------------------------------------------------
    def _pick_canary_environment(self):
        """Return the staging ``tenant.environment`` record or False.

        Defensive: ``tenant.environment`` is provided by ``tenant_infra``
        in Track A. If the model is not yet available we simply return
        False and callers fall back to legacy behaviour.
        """
        self.ensure_one()
        if self.environment_id:
            return self.environment_id
        try:
            Env = self.env["tenant.environment"].sudo()
        except KeyError:
            return False
        env = Env.search(
            [
                ("tenant_id", "=", self.tenant_id.id),
                ("env_type", "=", "staging"),
            ],
            limit=1,
        )
        return env or False

    @api.model
    def _log_audit(self, rec, event_type, success=True, error=None, extra=None):
        try:
            payload = {
                "deployment_id": rec.id,
                "module": rec.catalog_id.module_name,
                "tenant": rec.tenant_id.slug,
                "mode": rec.deploy_mode,
                "canary_phase": rec.canary_phase,
                "error": error,
            }
            if extra:
                payload.update(extra)
            self.env["custom.hub.audit.event"].sudo().log(
                event_type=event_type,
                tenant_id=rec.tenant_id.id,
                summary=(
                    f"{event_type} {rec.catalog_id.module_name} on "
                    f"{rec.tenant_id.slug} → "
                    f"{'OK' if success else 'FAILED'}"
                ),
                payload=payload,
                object_ref=f"custom.hub.module.deployment,{rec.id}",
            )
        except Exception as exc:  # pragma: no cover - defensive
            _logger.debug("[hub_deploy] audit log skipped: %s", exc)
