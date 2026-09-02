# -*- coding: utf-8 -*-
from __future__ import annotations

from odoo import api, fields, models
from odoo.exceptions import UserError

from .adapter_registry import get_adapter_class, list_adapter_classes


class CustomAdapterConfig(models.Model):
    _name = "custom.adapter.config"
    _description = "External Adapter Configuration"
    _inherit = ["pdp.audited.mixin", "mail.thread"]
    _order = "name"

    name = fields.Char(required=True, index=True, tracking=True)
    adapter_type = fields.Selection(
        selection="_selection_adapter_type",
        required=True,
        tracking=True,
        help="Registered adapter implementation class.",
    )
    base_url = fields.Char(required=True, tracking=True)
    auth_method = fields.Selection(
        [("none", "None"), ("hmac", "HMAC-SHA256"), ("bearer", "Bearer"), ("basic", "Basic")],
        default="hmac",
        required=True,
        tracking=True,
    )
    credential_ref = fields.Char(
        string="Credential ir.config_parameter Key",
        help="Key in ir.config_parameter holding the secret. Stored encrypted at-rest "
        "via the platform Fernet layer when prefixed with 'enc:'.",
    )
    timeout_s = fields.Integer(string="Timeout (s)", default=15, required=True)
    retry_count = fields.Integer(default=3, required=True)
    circuit_breaker_threshold = fields.Integer(default=5, required=True)
    circuit_breaker_cooldown_s = fields.Integer(string="CB Cooldown (s)", default=60, required=True)
    consecutive_failures = fields.Integer(default=0, readonly=True)
    circuit_opened_at = fields.Datetime(readonly=True)
    status = fields.Selection(
        [("active", "Active"), ("disabled", "Disabled"), ("circuit_open", "Circuit Open")],
        default="active",
        required=True,
        tracking=True,
        index=True,
    )
    last_health_check = fields.Datetime(readonly=True)
    last_health_ok = fields.Boolean(readonly=True)
    notes = fields.Text()

    _name_uniq = models.Constraint(
        "unique(name)",
        "Adapter config name must be unique.",
    )

    @api.model
    def _selection_adapter_type(self) -> list[tuple[str, str]]:
        classes = list_adapter_classes()
        return [(c, c) for c in classes] or [("noop", "noop")]

    def get_adapter(self):
        self.ensure_one()
        if self.status == "disabled":
            raise UserError(f"Adapter '{self.name}' is disabled.")
        cls = get_adapter_class(self.adapter_type)
        if cls is None:
            raise UserError(f"Adapter type '{self.adapter_type}' is not registered.")
        return cls(self)

    def action_health_check(self):
        for rec in self:
            adapter = rec.get_adapter()
            resp = adapter.health_check()
            rec.write(
                {
                    "last_health_check": fields.Datetime.now(),
                    "last_health_ok": bool(resp.ok),
                }
            )
        return True

    def action_reset_circuit(self):
        self.write(
            {
                "status": "active",
                "consecutive_failures": 0,
                "circuit_opened_at": False,
            }
        )
        return True

    def action_disable(self):
        self.write({"status": "disabled"})
        return True

    def action_enable(self):
        self.write({"status": "active", "consecutive_failures": 0, "circuit_opened_at": False})
        return True
