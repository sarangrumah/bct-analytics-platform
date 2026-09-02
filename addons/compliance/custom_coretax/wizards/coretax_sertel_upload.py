# -*- coding: utf-8 -*-
"""Sertifikat Elektronik (.p12) upload wizard.

The password field is *transient* (never stored). The .p12 payload is
encrypted with the env-keyed Fernet provided by `custom.ir.config` and the
ciphertext is stored in `ir.config_parameter` under the key
``coretax.sertel.<config_id>``. The wizard audit-logs every access as
``sertel_access`` per UU 27/2022.
"""

from __future__ import annotations

import base64
import json
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CoretaxSertelUploadWizard(models.TransientModel):
    _name = "custom.coretax.sertel.upload.wizard"
    _description = "Coretax Sertifikat Elektronik Upload"

    config_id = fields.Many2one(
        "custom.coretax.config",
        string="Coretax Configuration",
        required=True,
        default=lambda self: self.env["custom.coretax.config"].search([("active", "=", True)], limit=1),
    )
    p12_filename = fields.Char(string="Filename")
    p12_data = fields.Binary(string="Sertel (.p12)", required=True, attachment=False)
    p12_password = fields.Char(
        string="Sertel Password",
        help="Used only to validate the certificate on upload. Never persisted.",
    )
    sertel_expiry = fields.Date(string="Expiry (optional)")

    result_message = fields.Text(string="Result", readonly=True)

    def action_store(self):
        self.ensure_one()
        if not self.p12_data:
            raise UserError(_("Upload a .p12 file first."))
        if not self.config_id:
            raise UserError(_("Select a Coretax configuration."))

        try:
            raw = base64.b64decode(self.p12_data)
        except (ValueError, TypeError) as exc:
            raise UserError(_("Invalid .p12 payload: %s") % exc) from exc

        # Optional sanity check: try to load via cryptography if available.
        try:
            from cryptography.hazmat.primitives.serialization import pkcs12

            pkcs12.load_key_and_certificates(
                raw,
                (self.p12_password or "").encode() or None,
            )
        except ImportError:
            _logger.warning("cryptography pkcs12 not available — skipping validation")
        except Exception as exc:  # noqa: BLE001 — bubble friendly error
            raise UserError(_("Failed to open .p12 with provided password: %s") % exc) from exc

        # Store ciphertext via custom.ir.config (env-keyed Fernet).
        key = f"coretax.sertel.{self.config_id.id}"
        self.env["custom.ir.config"].set_encrypted(key, base64.b64encode(raw).decode())

        update_vals = {"sertel_filename": self.p12_filename}
        if self.sertel_expiry:
            update_vals["sertel_expiry"] = self.sertel_expiry
        self.config_id.sudo().write(update_vals)

        self._audit_sertel_access("upload", self.config_id.id, self.p12_filename)

        self.write(
            {
                "result_message": _(
                    "Sertel stored encrypted under ir.config_parameter key %s. Password was not persisted."
                )
                % key,
                "p12_password": False,  # scrub from memory
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    # ----- Audit log -----
    def _audit_sertel_access(self, op: str, config_id: int, filename: str | None) -> None:
        cr = self.env.cr
        payload = json.dumps(
            {
                "operation": op,
                "config_id": config_id,
                "filename": filename or "",
            }
        )
        cr.execute(
            """
            INSERT INTO pdp.audit_log
                (actor_user_id, actor_login, tenant_db, model_name, res_id,
                 action, field_changes, classification, reason)
            VALUES (%s, %s, %s, %s, %s, 'sertel_access', %s::jsonb, 'sensitive_pii', %s)
            """,
            (
                self.env.uid,
                self.env.user.login,
                cr.dbname,
                "custom.coretax.config",
                config_id,
                payload,
                "Coretax sertel %s by %s" % (op, self.env.user.login),
            ),
        )
