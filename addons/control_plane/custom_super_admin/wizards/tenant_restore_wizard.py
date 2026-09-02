# -*- coding: utf-8 -*-
"""Wizard: restore a tenant backup to a (typically staging) DB."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class TenantRestoreWizard(models.TransientModel):
    _name = "tenant.restore.wizard"
    _description = "Restore a backup"

    tenant_id = fields.Many2one("tenant.registry", required=True, ondelete="cascade")
    slug = fields.Char(related="tenant_id.slug", readonly=True)
    backup_id = fields.Many2one(
        "tenant.backup",
        domain="[('tenant_id', '=', tenant_id), ('outcome', '=', 'success')]",
        required=True,
    )
    s3_key = fields.Char(related="backup_id.s3_key", readonly=True)
    target_db = fields.Char(
        help="Target DB name. Defaults to '<slug>_staging' which is safe (non-destructive against live tenant).",
    )

    confirm_destructive = fields.Boolean(
        string="I understand this is destructive",
        help="Required if target_db equals the live tenant db_name.",
    )

    @api.onchange("tenant_id")
    def _onchange_default_target(self):
        for rec in self:
            if rec.tenant_id and not rec.target_db:
                rec.target_db = f"{rec.tenant_id.slug}_staging"

    def action_restore(self):
        self.ensure_one()
        if not self.backup_id:
            raise UserError(_("Pick a backup to restore."))
        target = (self.target_db or f"{self.slug}_staging").strip()
        if target == self.tenant_id.db_name and not self.confirm_destructive:
            raise UserError(
                _(
                    "Target DB equals the live tenant DB — tick the "
                    "destructive-action confirmation if this is intentional."
                )
            )
        client = self.env["custom.super.admin.orchestrator.client"].sudo()
        try:
            result = client.restore_backup(self.slug, s3_key=self.s3_key, target_db=target)
        except Exception as e:
            raise UserError(_("Restore failed: %s") % e) from e
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Restore complete"),
                "message": _("Restored to DB '%s'.") % result.get("restored_to_db"),
                "type": "success",
                "sticky": True,
            },
        }
