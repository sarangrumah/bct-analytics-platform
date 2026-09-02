# -*- coding: utf-8 -*-
"""Wizard: provision a new tenant via the orchestrator API."""

from __future__ import annotations

import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")


class TenantProvisionWizard(models.TransientModel):
    _name = "tenant.provision.wizard"
    _description = "Provision a new tenant"

    slug = fields.Char(
        required=True,
        help="Lowercase identifier 2-63 chars (letters/digits/underscore, "
        "must start with a letter). Will also be the DB name and the "
        "subdomain (e.g. acme → acme.platform.localhost).",
    )
    display_name = fields.Char(required=True)
    plan_tier = fields.Selection(
        [("trial", "Trial"), ("standard", "Standard"), ("enterprise", "Enterprise")],
        default="standard",
        required=True,
    )
    contact_email = fields.Char()
    contact_phone = fields.Char()
    csm_user_id = fields.Many2one("res.users", string="CSM", default=lambda self: self.env.user)
    backup_schedule_cron = fields.Char(default="0 2 * * *", help="Standard 5-field cron expression.")
    feature_pajakku = fields.Boolean(string="Enable Pajakku Coretax adapter", default=False)
    feature_marketplace = fields.Boolean(string="Enable marketplace vertical", default=False)
    install_modules_extra = fields.Char(
        string="Additional modules",
        help="Comma-separated list of extra module names to install beyond the default set.",
    )

    # Output (read-only after run)
    admin_password = fields.Char(readonly=True)
    fernet_key_dek = fields.Char(readonly=True)
    run_done = fields.Boolean(readonly=True)

    @api.constrains("slug")
    def _check_slug(self):
        for rec in self:
            if rec.slug and not SLUG_RE.match(rec.slug):
                raise ValidationError(
                    _("Slug must match %s (lowercase letters/digits/underscore, start with a letter, length 2-63).")
                    % SLUG_RE.pattern
                )

    def _merge_install_modules(self, modules, payload):
        """Hook for downstream modules to merge extra module sets into the
        provisioning install list. ``custom_hub_console`` overrides this to add
        the selected industry pack's modules. Base implementation is a no-op."""
        return modules

    def action_provision(self):
        self.ensure_one()
        payload = {
            "slug": self.slug,
            "display_name": self.display_name,
            "plan_tier": self.plan_tier,
            "contact_email": self.contact_email or None,
            "contact_phone": self.contact_phone or None,
            "csm_user_id": self.csm_user_id.id if self.csm_user_id else None,
            "features": {
                "pajakku": self.feature_pajakku,
                "marketplace": self.feature_marketplace,
            },
            "backup_schedule_cron": self.backup_schedule_cron or None,
        }

        # Default platform module set (mirrors DEFAULT_TENANT_MODULES on the
        # orchestrator). We only override install_modules when a pack or extra
        # modules are chosen; otherwise the orchestrator applies its defaults.
        from_defaults = [
            "custom_core",
            "custom_currency_nbsp",
            "custom_ai_bridge",
            "custom_pdp_taxonomy",
            "custom_pdp_audit",
            "custom_pdp_consent",
            "custom_pdp_dsar",
            "custom_pdp_masking",
            "custom_pdp_retention",
            "custom_coretax",
        ]
        modules = list(from_defaults)
        # Hook: custom_hub_console merges industry-pack modules here. Kept as a
        # hook so super_admin carries no dependency on custom_hub_console (which
        # depends on super_admin — the reverse would break registry load order).
        modules = self._merge_install_modules(modules, payload)
        extra = (self.install_modules_extra or "").strip()
        if extra:
            modules += [m.strip() for m in extra.split(",") if m.strip()]
        if len(modules) > len(from_defaults):
            # De-dup, order-preserving (defaults first, then pack deps-first, then extras).
            seen = set()
            payload["install_modules"] = [m for m in modules if not (m in seen or seen.add(m))]

        client = self.env["custom.super.admin.orchestrator.client"].sudo()
        try:
            result = client.provision(payload)
        except Exception as e:
            raise UserError(_("Provision failed: %s") % e) from e

        # Mirror result back so ops can capture credentials ONCE
        self.write(
            {
                "admin_password": result.get("admin_password"),
                "fernet_key_dek": result.get("fernet_key_dek"),
                "run_done": True,
            }
        )

        # Trigger immediate sync so the new tenant appears in the list
        self.env["tenant.registry"].sudo()._cron_sync_from_orchestrator()

        # Re-open the same wizard form so admin_password is visible
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": dict(self.env.context, form_view_initial_mode="readonly"),
        }
