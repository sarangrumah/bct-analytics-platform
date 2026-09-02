# -*- coding: utf-8 -*-
"""Add industry-pack selection to the tenant provisioning wizard.

The ``pack_id`` field lives here rather than in ``custom_super_admin`` because
the ``custom.hub.industry.pack`` model is defined in this module, and
custom_hub_console depends on custom_super_admin (not the reverse). Declaring
the Many2one in super_admin would invert that dependency and break the registry
load order (the comodel would not yet exist when super_admin loads).
"""

from odoo import fields, models


class TenantProvisionWizard(models.TransientModel):
    _inherit = "tenant.provision.wizard"

    pack_id = fields.Many2one(
        "custom.hub.industry.pack",
        string="Industry Pack",
        help="Curated module bundle for a business vertical. Its modules are "
        "merged into the install set; the orchestrator resolves Odoo deps.",
    )

    def _merge_install_modules(self, modules, payload):
        modules = super()._merge_install_modules(modules, payload)
        if self.pack_id:
            modules += self.pack_id.resolve_module_names()
            payload["features"]["industry_pack"] = self.pack_id.code
        return modules
