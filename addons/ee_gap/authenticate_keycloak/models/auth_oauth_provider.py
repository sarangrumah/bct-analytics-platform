# -*- coding: utf-8 -*-
"""Authorization-code flow settings on the standard OAuth provider.

Config is per-tenant (it lives on the provider record in the tenant DB), so the
same image serves every tenant. The client secret never lands in a column: it is
written through ``custom.ir.config`` (Fernet) and only read server-side during
the token exchange.
"""

from __future__ import annotations

from odoo import api, fields, models

# ir.config_parameter-style key, one secret per provider record.
SECRET_KEY_TMPL = "authenticate_keycloak.client_secret.%s"


class AuthOauthProvider(models.Model):
    _inherit = "auth.oauth.provider"

    flow = fields.Selection(
        [
            ("token", "Implicit / Token (stock)"),
            ("authorization_code", "Authorization Code (confidential client)"),
        ],
        default="token",
        required=True,
        help="Stock Odoo only speaks the implicit flow. Choose Authorization Code "
        "when the IdP client has Client Authentication on and issues a client "
        "secret (e.g. a confidential Keycloak client).",
    )
    token_endpoint = fields.Char(
        string="Token URL",
        help="OIDC token endpoint, e.g. "
        "https://keycloak.example.com/realms/<realm>/protocol/openid-connect/token. "
        "Required for the authorization-code flow.",
    )
    client_secret = fields.Char(
        compute="_compute_client_secret",
        inverse="_inverse_client_secret",
        store=False,
        help="Stored encrypted (Fernet) in this tenant's database, not in this "
        "column. Leave blank to keep the existing secret.",
    )

    def _secret_key(self) -> str:
        self.ensure_one()
        return SECRET_KEY_TMPL % self.id

    @api.depends("flow")
    def _compute_client_secret(self):
        IrCfg = self.env["custom.ir.config"].sudo()
        for provider in self:
            if not provider.id:
                provider.client_secret = False
                continue
            provider.client_secret = IrCfg.get_encrypted(provider._secret_key()) or False

    def _inverse_client_secret(self):
        IrCfg = self.env["custom.ir.config"].sudo()
        for provider in self:
            value = (provider.client_secret or "").strip()
            # Saving the form without retyping the secret must not wipe it.
            if value:
                IrCfg.set_encrypted(provider._secret_key(), value)

    def _get_client_secret(self) -> str:
        """Server-side read of the decrypted secret for the token exchange."""
        self.ensure_one()
        return self.env["custom.ir.config"].sudo().get_encrypted(self._secret_key()) or ""
