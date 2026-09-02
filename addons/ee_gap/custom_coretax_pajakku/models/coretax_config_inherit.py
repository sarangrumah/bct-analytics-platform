# -*- coding: utf-8 -*-
"""Extend ``custom.coretax.config`` with Pajakku-specific fields + helpers.

Credentials are encrypted at rest via ``custom.ir.config`` (Fernet wrapped
with master KMS key, same as sertel). They are never persisted in
plaintext on this record; the secret-set flag indicates presence.
"""

from __future__ import annotations

from odoo import fields, models


class CoretaxConfigPajakku(models.Model):
    _inherit = "custom.coretax.config"

    # Extend adapter_type selection
    adapter_type = fields.Selection(
        selection_add=[("pajakku", "Pajakku (host-to-host ASPP)")],
        ondelete={"pajakku": "set default"},
    )

    pajakku_enabled = fields.Boolean(
        string="Pajakku enabled",
        help="Master switch. Even with credentials set, the adapter will refuse to "
        "send while this is off — useful kill-switch during incidents.",
    )
    pajakku_api_url = fields.Char(
        string="Pajakku API URL",
        help="Leave empty to use the standard sandbox / production URL based on the sandbox toggle below.",
    )
    pajakku_sandbox_mode = fields.Boolean(
        string="Sandbox mode",
        default=True,
        help="Until production credentials are validated, keep this on so accidental "
        "submissions don't reach the live DJP environment.",
    )
    pajakku_client_id = fields.Char(string="Pajakku Client ID")

    # Secret presence indicator — actual ciphertext in ir.config_parameter
    pajakku_client_secret_set = fields.Boolean(
        string="Client Secret Set",
        compute="_compute_pajakku_secret_set",
        store=False,
    )

    # Stats (computed)
    pajakku_pending_tx = fields.Integer(compute="_compute_pajakku_counts")
    pajakku_error_tx = fields.Integer(compute="_compute_pajakku_counts")
    pajakku_last_test = fields.Datetime(string="Last connection test")
    pajakku_last_test_ok = fields.Boolean()
    pajakku_last_test_message = fields.Char(readonly=True)

    # ------------------------------------------------------------------

    def _pajakku_param_key(self) -> str:
        self.ensure_one()
        return f"custom_coretax_pajakku.client_secret.{self.id}"

    def _compute_pajakku_secret_set(self):
        IrCfg = self.env["custom.ir.config"]
        for rec in self:
            try:
                v = IrCfg.sudo().get_encrypted(rec._pajakku_param_key())
                rec.pajakku_client_secret_set = bool(v)
            except Exception:
                rec.pajakku_client_secret_set = False

    def _compute_pajakku_counts(self):
        Tx = self.env["custom.coretax.transaction"].sudo()
        for rec in self:
            rec.pajakku_pending_tx = Tx.search_count(
                [
                    ("config_id", "=", rec.id),
                    ("state", "in", ("queued", "submitting", "submitted")),
                ]
            )
            rec.pajakku_error_tx = Tx.search_count(
                [
                    ("config_id", "=", rec.id),
                    ("state", "in", ("error", "rejected")),
                ]
            )

    # ------------------------------------------------------------------
    # Public helpers — used by the Pajakku adapter
    # ------------------------------------------------------------------

    def _pajakku_get_client_secret(self) -> str | None:
        self.ensure_one()
        IrCfg = self.env["custom.ir.config"]
        try:
            return IrCfg.sudo().get_encrypted(self._pajakku_param_key())
        except Exception:
            return None

    def action_pajakku_set_secret(self):
        """Open a small transient wizard to capture the secret then encrypt it."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "custom.coretax.pajakku.secret.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_config_id": self.id},
        }

    def action_pajakku_test_connection(self):
        self.ensure_one()
        Adapter = self.env["custom.coretax.adapter.pajakku"]
        result = Adapter.test_connection(self)
        self.write(
            {
                "pajakku_last_test": fields.Datetime.now(),
                "pajakku_last_test_ok": result.get("ok", False),
                "pajakku_last_test_message": result.get("message", "")[:240],
            }
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Pajakku connection",
                "message": result.get("message", ""),
                "type": "success" if result.get("ok") else "danger",
                "sticky": not result.get("ok"),
            },
        }


# ----- Lightweight wizard to capture the secret -----


class CoretaxPajakkuSecretWizard(models.TransientModel):
    _name = "custom.coretax.pajakku.secret.wizard"
    _description = "Capture + encrypt Pajakku client secret"

    config_id = fields.Many2one("custom.coretax.config", required=True)
    client_secret = fields.Char(required=True)

    def action_save(self):
        self.ensure_one()
        IrCfg = self.env["custom.ir.config"]
        IrCfg.sudo().set_encrypted(
            self.config_id._pajakku_param_key(),
            self.client_secret,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Pajakku secret",
                "message": "Stored (encrypted). You can delete this wizard now.",
                "type": "success",
            },
        }
