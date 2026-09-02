# -*- coding: utf-8 -*-
"""HR SSO settings, surfaced under Settings -> Custom Platform.

Per-tenant config for the Keycloak HR sync. The HC API key is stored **encrypted**
via ``custom.ir.config`` (never plaintext); the base URL and JIT toggle are plain
``ir.config_parameter`` values.
"""

from __future__ import annotations

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    hr_sso_hc_base_url = fields.Char(
        string="HC API Base URL",
        config_parameter="hc.base_url",
        help="Base URL of the HC employee API, with trailing slash, e.g. "
        "https://hc.example.com/ . Endpoint called: "
        "{base}api/v1/open-api/employees/{nik}. Leave empty to skip API enrichment.",
    )
    hr_sso_hc_api_key = fields.Char(
        string="HC API Key",
        help="Sent as the X-API-Key header. Stored encrypted (Fernet) in this "
        "tenant's DB. Leave blank to keep the existing key.",
    )
    hr_sso_jit_create = fields.Boolean(
        string="Auto-create users on SSO (JIT)",
        config_parameter="custom_hr_sso_keycloak.jit_create",
        help="Off (default): only users that already exist in Odoo can sign in via "
        "Keycloak. On: unknown identities are created through standard OAuth signup.",
    )

    def get_values(self):
        res = super().get_values()
        res["hr_sso_hc_api_key"] = self.env["custom.ir.config"].get_encrypted("hc.api_key") or ""
        return res

    def set_values(self):
        super().set_values()
        # Only (re)write when a value is supplied, so saving settings without
        # re-typing the key does not clobber the stored secret.
        value = (self.hr_sso_hc_api_key or "").strip()
        if value:
            self.env["custom.ir.config"].set_encrypted("hc.api_key", value)
