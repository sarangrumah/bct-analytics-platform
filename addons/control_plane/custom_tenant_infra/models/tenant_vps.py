# -*- coding: utf-8 -*-
"""``tenant.vps`` — VPS inventory + state machine.

The action_* methods delegate to ``tenant.vps.deployer`` which wraps the
HMAC-signed orchestrator client. SSH credentials are NEVER persisted in
plaintext; ``ssh_credential_ref`` is a pointer (e.g. ``vault://prod/vps/{id}/ssh_key``)
that the orchestrator resolves at SSH-time.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


VPS_STATES = [
    ("registered", "Registered"),
    ("hardening", "Hardening"),
    ("bootstrapping", "Bootstrapping"),
    ("active", "Active"),
    ("degraded", "Degraded"),
    ("decommissioned", "Decommissioned"),
]

# Allowed forward transitions. Decommission is always allowed from any
# live state; degraded↔active is bidirectional.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "registered": {"hardening", "decommissioned"},
    "hardening": {"bootstrapping", "registered", "decommissioned"},
    "bootstrapping": {"active", "hardening", "decommissioned"},
    "active": {"degraded", "decommissioned"},
    "degraded": {"active", "decommissioned"},
    "decommissioned": set(),
}


class TenantVps(models.Model):
    _name = "tenant.vps"
    _description = "Tenant VPS"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"
    _rec_name = "name"

    name = fields.Char(required=True, tracking=True)
    hostname = fields.Char(required=True, tracking=True)
    public_ip = fields.Char(tracking=True)
    ssh_port = fields.Integer(default=22, required=True)
    ssh_user = fields.Char(default="root", required=True)
    ssh_credential_ref = fields.Char(
        string="SSH Credential Ref",
        required=True,
        help=(
            "Pointer to credential vault (e.g. 'vault://prod/vps/{id}/ssh_key' "
            "or 'env://VPS_SSH_KEY_PATH'). NEVER store private key material here."
        ),
    )

    provider = fields.Selection(
        [
            ("biznet", "Biznet"),
            ("idcloudhost", "IDCloudHost"),
            ("digitalocean", "DigitalOcean"),
            ("hetzner", "Hetzner"),
            ("aws", "AWS"),
            ("other", "Other"),
        ],
        default="other",
    )
    region = fields.Char()
    cpu_cores = fields.Integer(string="CPU Cores", default=0)
    ram_mb = fields.Integer(string="RAM (MB)", default=0)
    disk_gb = fields.Integer(string="Disk (GB)", default=0)

    os_version = fields.Char(help="Detected via SSH facter on bootstrap")
    docker_version = fields.Char()

    state = fields.Selection(
        VPS_STATES,
        default="registered",
        required=True,
        tracking=True,
        index=True,
    )

    prometheus_target_url = fields.Char(string="Prometheus Target URL")
    grafana_dashboard_uid = fields.Char(string="Grafana Dashboard UID")

    bootstrap_log = fields.Text(
        help="Append-only log stream from orchestrator (SSE consumed by OWL).",
    )
    last_health_check_at = fields.Datetime()

    environment_ids = fields.One2many(
        "tenant.environment",
        "vps_id",
        string="Environments",
    )
    environment_count = fields.Integer(
        compute="_compute_environment_count",
        store=False,
    )

    _hostname_unique = models.Constraint(
        "unique(hostname)",
        "VPS hostname must be unique.",
    )

    # ------------------------------------------------------------------

    @api.depends("environment_ids")
    def _compute_environment_count(self):
        for rec in self:
            rec.environment_count = len(rec.environment_ids)

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _assert_transition(self, new_state: str) -> None:
        self.ensure_one()
        allowed = ALLOWED_TRANSITIONS.get(self.state, set())
        if new_state not in allowed and new_state != self.state:
            raise UserError(_("Invalid VPS state transition: %s → %s") % (self.state, new_state))

    def _set_state(self, new_state: str) -> None:
        self.ensure_one()
        if new_state == self.state:
            return
        self._assert_transition(new_state)
        self.write({"state": new_state})

    def _append_log(self, line: str) -> None:
        self.ensure_one()
        ts = fields.Datetime.now().isoformat()
        existing = self.bootstrap_log or ""
        self.write({"bootstrap_log": f"{existing}[{ts}] {line}\n"})

    # ------------------------------------------------------------------
    # Action buttons (delegate to deployer)
    # ------------------------------------------------------------------

    def _deployer(self):
        return self.env["tenant.vps.deployer"].sudo()

    def action_register_with_orchestrator(self):
        for rec in self:
            try:
                self._deployer().register(rec)
                rec._append_log("Registered with orchestrator")
            except Exception as e:  # noqa: BLE001
                rec._append_log(f"register failed: {e}")
                raise UserError(_("Register failed: %s") % e) from e
        return True

    def action_bootstrap(self):
        for rec in self:
            rec._set_state("hardening")
            try:
                self._deployer().bootstrap(rec)
                rec._set_state("bootstrapping")
                rec._append_log("Bootstrap completed (hardening + docker + caddy)")
                rec._set_state("active")
            except Exception as e:  # noqa: BLE001
                rec._append_log(f"bootstrap failed: {e}")
                raise UserError(_("Bootstrap failed: %s") % e) from e
        return True

    def action_deploy_odoo_stack(self):
        for rec in self:
            if rec.state not in ("active", "degraded"):
                raise UserError(_("VPS must be active before deploying a stack (current: %s)") % rec.state)
            envs = rec.environment_ids.filtered(lambda e: e.env_type in ("dev", "staging", "prod"))
            if not envs:
                raise UserError(_("No environments linked to this VPS."))
            for env in envs:
                try:
                    self._deployer().deploy_stack(rec, env)
                    rec._append_log(f"Deployed stack for env={env.env_type} db={env.db_name}")
                except Exception as e:  # noqa: BLE001
                    rec._append_log(f"deploy_stack failed env={env.env_type}: {e}")
                    raise UserError(_("Deploy stack failed: %s") % e) from e
        return True

    def action_sync_addons(self):
        for rec in self:
            for env in rec.environment_ids:
                try:
                    self._deployer().sync_addons(rec, env)
                    rec._append_log(f"Synced addons for env={env.env_type}")
                except Exception as e:  # noqa: BLE001
                    rec._append_log(f"sync_addons failed: {e}")
                    raise UserError(_("Sync addons failed: %s") % e) from e
        return True

    def action_healthcheck(self):
        for rec in self:
            try:
                result = self._deployer().healthcheck(rec)
                rec.write({"last_health_check_at": fields.Datetime.now()})
                ok = bool(result.get("ok"))
                if not ok and rec.state == "active":
                    rec._set_state("degraded")
                elif ok and rec.state == "degraded":
                    rec._set_state("active")
                rec._append_log(f"healthcheck ok={ok}")
            except Exception as e:  # noqa: BLE001
                rec._append_log(f"healthcheck failed: {e}")
                raise UserError(_("Healthcheck failed: %s") % e) from e
        return True

    def action_decommission(self):
        for rec in self:
            try:
                self._deployer().decommission(rec)
                rec._set_state("decommissioned")
                rec._append_log("Decommissioned")
            except Exception as e:  # noqa: BLE001
                rec._append_log(f"decommission failed: {e}")
                raise UserError(_("Decommission failed: %s") % e) from e
        return True
