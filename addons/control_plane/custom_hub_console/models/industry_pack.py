# -*- coding: utf-8 -*-
"""Industry Pack: a curated, named bundle of modules tied to a business
vertical (like odoo.com industry packages).

A pack groups ``custom.hub.module.catalog`` rows — both platform custom
modules and indexed Odoo standard apps — so an operator can deploy the
whole bundle to a tenant in a single action instead of one module at a
time. The same pack drives:

* admin post-launch batch deploy (``deploy_to_tenant`` / Deploy Pack wizard),
* onboarding intake pre-selection (read by the portal ``/api/packs``),
* initial provisioning (``tenant.provision.wizard.pack_id``).

Pack definitions live in the DB and are editable in the Hub UI. Seed
packs ship as XML metadata (empty ``module_ids``); ``_link_seed_modules``
fills them from ``_SEED_PACK_MODULES`` after the catalog scan, only when
empty, so operator edits are preserved.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# Seed pack membership (code -> module technical names). ``code`` mirrors a
# hub-portal ``verticals`` value so intake can match a pack by vertical.
# Names are resolved to catalog rows by ``_link_seed_modules``; names absent
# from the catalog (e.g. Enterprise-only apps on a CE install) are skipped.
_SEED_PACK_MODULES = {
    "retail": [
        "custom_accounting_full",
        "custom_coretax",
        "custom_coretax_bupot",
        "custom_pos_id",
        "custom_retail_pos_offline",
        "custom_wms_putaway",
        "custom_wms_cycle_count",
        "custom_approval_engine",
        "point_of_sale",
        "stock",
        "sale_management",
        "crm",
        "website_sale",
    ],
    "fnb": [
        "custom_accounting_full",
        "custom_coretax",
        "custom_pos_id",
        "custom_retail_pos_offline",
        "custom_subscription",
        "custom_appointments",
        # ESB Core integration: stock opname, demand forecast, auto replenishment.
        "custom_esb_connector",
        "custom_fnb_stock_ops",
        "custom_wms_cycle_count",
        "point_of_sale",
        "stock",
        "crm",
    ],
    "drone_services": [
        "custom_accounting_full",
        "custom_coretax",
        "custom_drone_show",
        "custom_pilot_procurement",
        "custom_flight_permission",
        "custom_drone_fleet",
        "custom_approval_engine",
        "project",
        "crm",
        "sale_management",
    ],
    "drone_rental": [
        "custom_accounting_full",
        "custom_coretax",
        "custom_rental",
        "custom_rental_drone",
        "custom_damage_claim",
        "custom_drone_repair",
        "custom_drone_kit",
        "custom_drone_fleet",
        "sale_management",
        "stock",
        "maintenance",
    ],
    "distrib": [
        "custom_accounting_full",
        "custom_coretax",
        "custom_3way_match",
        "custom_wms_putaway",
        "custom_wms_cycle_count",
        "custom_wms_to_engine",
        "custom_hht_bridge",
        "custom_drone_dropship",
        "stock",
        "purchase",
        "sale_management",
    ],
    "manufacturing": [
        "custom_accounting_full",
        "custom_accounting_asset",
        "custom_coretax",
        "custom_3way_match",
        "custom_wms_putaway",
        "custom_wms_to_engine",
        "custom_planning",
        "mrp",
        "stock",
        "purchase",
        "maintenance",
    ],
    "services": [
        "custom_accounting_full",
        "custom_accounting_recurring",
        "custom_coretax",
        "custom_coretax_bupot",
        "custom_approval_engine",
        "custom_appointments",
        "custom_documents",
        "custom_subscription",
        "custom_helpdesk",
        "project",
        "hr_timesheet",
        "sale_management",
    ],
    # PPOB (Payment Point Online Bank) vertical. custom_ppob_oracle_bridge is
    # intentionally omitted -- it is a per-tenant opt-in (legacy EVShop bridge
    # with two 1-minute crons), enabled only where needed.
    # Biller adapters (custom_ppob_biller_*) are omitted for the same reason:
    # which biller a tenant sells through is a per-tenant commercial decision,
    # not a property of the vertical. Note the consequence -- the pack alone
    # cannot sell anything; a tenant must add at least one biller adapter.
    "ppob": [
        "custom_ppob_core",
        "custom_ppob_provider",
        "custom_ppob_wallet",
        "custom_ppob_sale",
        "custom_ppob_sla",
        "custom_ppob_commission",
        "custom_ppob_rollup",
        "custom_ppob_va",
        "custom_accounting_full",
        "custom_accounting_reports",
        "custom_coretax",
        "custom_coretax_bupot",
        "custom_pph_witholding",
        "custom_approval_engine",
        "custom_adapter_framework",
        "custom_tax_id",
        "sale_management",
        "account",
    ],
}


class CustomHubIndustryPack(models.Model):
    # access defined in security/ir.model.access.csv (viewer/csm/admin/super)
    _name = "custom.hub.industry.pack"  # nosemgrep
    _description = "Industry Pack (curated module bundle)"
    _order = "sequence, name"
    _rec_name = "name"

    name = fields.Char(required=True)
    code = fields.Char(
        required=True,
        index=True,
        help="Short vertical code; should match a hub-portal `verticals` "
        "value (retail, fnb, drone_services, …) so intake can match by vertical.",
    )
    vertical = fields.Char(string="Vertical Label")
    description = fields.Text()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    icon = fields.Char(help="Emoji or short glyph for portal cards.")
    color = fields.Integer()

    module_ids = fields.Many2many(
        comodel_name="custom.hub.module.catalog",
        relation="custom_hub_pack_catalog_rel",
        column1="pack_id",
        column2="catalog_id",
        string="Modules",
    )
    module_count = fields.Integer(compute="_compute_module_count", store=False)

    _code_uniq = models.Constraint(
        "unique(code)",
        "Industry pack code must be unique.",
    )

    @api.depends("module_ids")
    def _compute_module_count(self):
        for rec in self:
            rec.module_count = len(rec.module_ids)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------
    def resolve_module_names(self):
        """Ordered (deps-first), de-duped module technical names for this
        pack = the selected ``module_ids`` plus their transitive catalog
        dependencies. Stock deps without a catalog row are not listed here
        but still install via Odoo's own resolver."""
        self.ensure_one()
        Catalog = self.env["custom.hub.module.catalog"].sudo()
        # sudo the traversal so callers that can read the pack but not the
        # catalog (e.g. a super-admin provisioning operator) still resolve.
        return Catalog._toposort_module_names(self.sudo().module_ids)

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------
    def _deploy_pack(self, tenants, deploy_mode="install", enable_canary=True, skip_installed=True):
        """Create + run one ``custom.hub.module.deployment`` per pack module
        for each tenant, in dependency order. Reuses the deployment model's
        orchestrator calls and canary sequence — no orchestrator logic here.

        ``skip_installed`` (install mode only) drops modules already marked
        installed on the tenant. Returns the created deployment recordset."""
        self.ensure_one()
        Catalog = self.env["custom.hub.module.catalog"].sudo()
        Deployment = self.env["custom.hub.module.deployment"].sudo()

        ordered_names = self.resolve_module_names()
        cats = Catalog.search([("module_name", "in", ordered_names)])
        name_to_cat = {c.module_name: c for c in cats}

        created = Deployment.browse()
        for tenant in tenants:
            for name in ordered_names:
                cat = name_to_cat.get(name)
                if not cat:
                    continue
                if skip_installed and deploy_mode == "install":
                    already = Deployment.search_count(
                        [
                            ("catalog_id", "=", cat.id),
                            ("tenant_id", "=", tenant.id),
                            ("state", "=", "installed"),
                        ]
                    )
                    if already:
                        continue
                created |= Deployment.create(
                    {
                        "catalog_id": cat.id,
                        "tenant_id": tenant.id,
                        "deploy_mode": deploy_mode,
                        "state": "pending",
                    }
                )

        if created:
            # ``created`` preserves creation (deps-first) order.
            if enable_canary:
                created._run_canary_sequence()
            else:
                created.action_deploy()
        return created

    @api.model
    def deploy_to_tenant(self, pack_id, tenant_id, deploy_mode="install", enable_canary=True, skip_installed=True):
        """Portal-callable entry point: deploy a pack to one tenant.

        Returns the list of created ``custom.hub.module.deployment`` ids."""
        pack = self.browse(pack_id)
        if not pack.exists():
            raise UserError(_("Industry pack %s not found.") % pack_id)
        tenant = self.env["tenant.registry"].browse(tenant_id)
        if not tenant.exists():
            raise UserError(_("Tenant %s not found.") % tenant_id)
        rows = pack._deploy_pack(
            tenant,
            deploy_mode=deploy_mode,
            enable_canary=enable_canary,
            skip_installed=skip_installed,
        )
        return rows.ids

    def action_open_deploy_pack_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Deploy Pack"),
            "res_model": "custom.hub.deploy.pack.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_pack_id": self.id},
        }

    # ------------------------------------------------------------------
    # Seed linking
    # ------------------------------------------------------------------
    @api.model
    def _link_seed_modules(self):
        """Fill empty ``module_ids`` of seed packs from ``_SEED_PACK_MODULES``.

        Resolves technical names to catalog rows by name. Only packs whose
        ``module_ids`` is currently empty are touched (operator edits are
        preserved). Names absent from the catalog are silently skipped.
        Called at the end of the catalog scan so rows exist; idempotent."""
        Catalog = self.env["custom.hub.module.catalog"].sudo()
        linked = 0
        for code, names in _SEED_PACK_MODULES.items():
            pack = self.search([("code", "=", code)], limit=1)
            if not pack or pack.module_ids:
                continue
            cats = Catalog.search([("module_name", "in", names)])
            if cats:
                pack.module_ids = [(6, 0, cats.ids)]
                linked += 1
        if linked:
            _logger.info("[industry_pack] linked seed modules for %s pack(s)", linked)
        return linked
