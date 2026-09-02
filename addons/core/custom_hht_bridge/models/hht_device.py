# -*- coding: utf-8 -*-
# License: LGPL-3
from __future__ import annotations

import secrets

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class HhtDevice(models.Model):
    _name = "hht.device"
    _description = "Hybrid Handheld Terminal Device"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "last_seen_at desc, id desc"
    _rec_name = "name"

    name = fields.Char(required=True, tracking=True)
    device_id = fields.Char(
        string="Device Serial / ID",
        required=True,
        index=True,
        tracking=True,
        help="Unique physical/browser identifier (e.g. TC52-SN12345 or browser fp).",
    )
    model = fields.Selection(
        [
            ("zebra_tc21", "Zebra TC21"),
            ("zebra_tc52", "Zebra TC52"),
            ("zebra_tc72", "Zebra TC72"),
            ("honeywell_ct40", "Honeywell CT40"),
            ("generic_browser", "Generic Browser"),
            ("other", "Other"),
        ],
        default="generic_browser",
        required=True,
        tracking=True,
    )
    tenant_id = fields.Many2one(
        "tenant.registry",
        string="Tenant",
        ondelete="set null",
        index=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Default Operator",
        default=lambda self: self.env.user,
        required=True,
    )
    api_key = fields.Char(
        string="API Key",
        readonly=True,
        copy=False,
        index=True,
    )
    api_secret = fields.Char(
        string="API Secret",
        readonly=True,
        copy=False,
        help="Shared secret used for HMAC-SHA256 request signing.",
    )
    allowed_cidrs = fields.Char(
        string="Allowed CIDRs",
        help="Comma-separated CIDR blocks. Empty means no per-device restriction.",
    )
    enabled = fields.Boolean(default=True, tracking=True)
    last_seen_at = fields.Datetime(readonly=True)
    last_action_at = fields.Datetime(readonly=True)
    last_action_summary = fields.Char(readonly=True)
    scan_count_today = fields.Integer(
        string="Scans Today",
        compute="_compute_scan_count_today",
    )
    status = fields.Selection(
        [
            ("active", "Active"),
            ("disabled", "Disabled"),
            ("quarantined", "Quarantined"),
        ],
        compute="_compute_status",
        store=False,
    )
    scan_log_count = fields.Integer(compute="_compute_counts")
    sync_queue_count = fields.Integer(compute="_compute_counts")

    _device_id_tenant_uniq = models.Constraint(
        "unique(device_id, tenant_id)",
        "Device ID must be unique per tenant.",
    )

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    def _compute_scan_count_today(self):
        Log = self.env["hht.scan.log"]
        today_start = fields.Datetime.to_string(
            fields.Datetime.context_timestamp(self, fields.Datetime.now()).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        )
        for rec in self:
            rec.scan_count_today = Log.search_count(
                [
                    ("device_id", "=", rec.id),
                    ("scanned_at", ">=", today_start),
                ]
            )

    def _compute_status(self):
        for rec in self:
            if not rec.enabled:
                rec.status = "disabled"
            elif rec.scan_count_today and rec.scan_count_today > 10000:
                # heuristic: extreme volume -> quarantine
                rec.status = "quarantined"
            else:
                rec.status = "active"

    def _compute_counts(self):
        Log = self.env["hht.scan.log"]
        Q = self.env["hht.sync.queue"]
        for rec in self:
            rec.scan_log_count = Log.search_count([("device_id", "=", rec.id)])
            rec.sync_queue_count = Q.search_count([("device_id", "=", rec.id)])

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("api_key"):
                vals["api_key"] = secrets.token_hex(16)
            if not vals.get("api_secret"):
                vals["api_secret"] = secrets.token_hex(32)
        return super().create(vals_list)

    def write(self, vals):
        # Prevent silent overwrite of api_secret via UI/import.
        if "api_secret" in vals and not self.env.context.get("hht_allow_secret_write"):
            raise UserError(_("API secret is write-protected. Use Regenerate Secret action."))
        return super().write(vals)

    @api.constrains("allowed_cidrs")
    def _check_allowed_cidrs(self):
        import ipaddress

        for rec in self:
            if not rec.allowed_cidrs:
                continue
            for chunk in rec.allowed_cidrs.split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                try:
                    if "/" in chunk:
                        ipaddress.ip_network(chunk, strict=False)
                    else:
                        ipaddress.ip_address(chunk)
                except ValueError as e:
                    raise ValidationError(_("Invalid CIDR/IP %s: %s") % (chunk, e)) from e

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_regenerate_secret(self):
        if not self.env.user.has_group("custom_hht_bridge.group_hht_admin"):
            raise AccessError(_("Only HHT admins may regenerate device secrets."))
        for rec in self:
            new_secret = secrets.token_hex(32)
            rec.with_context(hht_allow_secret_write=True).write({"api_secret": new_secret})
            rec.message_post(body=_("API secret regenerated by %s") % self.env.user.name)
        return True

    def action_view_scan_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Scan Logs"),
            "res_model": "hht.scan.log",
            "view_mode": "list,form",
            "domain": [("device_id", "=", self.id)],
            "context": {"default_device_id": self.id},
        }

    def action_view_sync_queue(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Sync Queue"),
            "res_model": "hht.sync.queue",
            "view_mode": "list,form",
            "domain": [("device_id", "=", self.id)],
            "context": {"default_device_id": self.id},
        }

    # ------------------------------------------------------------------
    # Helpers used by controllers
    # ------------------------------------------------------------------
    def _touch_seen(self, summary: str | None = None):
        self.ensure_one()
        now = fields.Datetime.now()
        vals = {"last_seen_at": now}
        if summary:
            vals["last_action_at"] = now
            vals["last_action_summary"] = (summary or "")[:128]
        self.sudo().write(vals)

    @api.model
    def _find_by_api_key(self, api_key: str):
        if not api_key:
            return self.browse()
        return self.sudo().search(
            [
                ("api_key", "=", api_key),
                ("enabled", "=", True),
            ],
            limit=1,
        )

    @api.model
    def _cron_reset_scan_count_today(self):
        """Called hourly. The compute is on-demand; this exists so cron has a
        named hook and so we can invalidate any cached snapshots in future."""
        self.search([]).invalidate_recordset(["scan_count_today"])
        return True

    @api.model
    def _find_by_serial(self, serial: str):
        if not serial:
            return self.browse()
        return self.sudo().search(
            [
                ("device_id", "=", serial),
                ("enabled", "=", True),
            ],
            limit=1,
        )
