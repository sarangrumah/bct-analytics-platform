# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .constants import (
    DEFAULT_BASE_URL,
    DEFAULT_STATUS_MAX_AGE_DAYS,
    DEFAULT_STATUS_MIN_AGE_S,
)


class PpobProvider(models.Model):
    _inherit = "custom.ppob.provider"

    digiflazz_username = fields.Char(
        string="Digiflazz Username",
        help="The username configured in Digiflazz > Pengaturan Koneksi API. "
        "Part of every signature; a wrong value fails as an auth error, "
        "not a validation error.",
    )
    digiflazz_testing = fields.Boolean(
        string="Digiflazz Development Mode",
        help="Sends testing=true, so Digiflazz simulates instead of really "
        "selling. The mitra wallet is still debited for real on our side -- "
        "this flag only changes THEIR behaviour, never ours.",
    )
    digiflazz_status_min_age_s = fields.Integer(
        string="Status Min Age (s)",
        default=DEFAULT_STATUS_MIN_AGE_S,
        help="status() refuses to fire until this many seconds after dispatch. "
        "Digiflazz warns that repeat calls inside 1 minute cause a race "
        "condition or duplicate processing -- and because a prepaid status "
        "check IS a re-sent topup, that duplicate would be a real sale. "
        "Do not lower below 60 without a written answer from Digiflazz.",
    )
    digiflazz_status_max_age_days = fields.Integer(
        string="Status Max Age (days)",
        default=DEFAULT_STATUS_MAX_AGE_DAYS,
        help="status() refuses beyond this age. Past Digiflazz's retention a "
        "re-sent ref_id is no longer recognised as the original, so it "
        "books a BRAND-NEW transaction and charges the deposit. Leaving an "
        "ancient transaction unresolved for ops is cheap; a silent "
        "duplicate sale is not.",
    )

    @api.constrains("digiflazz_status_min_age_s", "digiflazz_status_max_age_days", "adapter_class")
    def _check_digiflazz_guards(self):
        for provider in self:
            if provider.adapter_class != "ppob_digiflazz":
                continue
            if provider.digiflazz_status_min_age_s < 1:
                raise ValidationError(
                    _(
                        "Digiflazz status min age must be at least 1 second. A "
                        "status check re-sends the topup; firing it immediately "
                        "risks a duplicate sale."
                    )
                )
            if provider.digiflazz_status_max_age_days < 1:
                raise ValidationError(_("Digiflazz status max age must be at least 1 day."))

    def _digiflazz_base_url(self):
        self.ensure_one()
        cfg = self.adapter_config_id
        return ((cfg.base_url if cfg else self.endpoint_url) or DEFAULT_BASE_URL).rstrip("/")

    def _digiflazz_api_key(self):
        """Resolve the API key from ir.config_parameter, keeping the secret out
        of the provider record (same pattern as ppob_http_json)."""
        self.ensure_one()
        cfg = self.adapter_config_id
        key = (cfg.credential_ref if cfg else self.credential_ref) or ""
        if not key:
            return ""
        return self.env["ir.config_parameter"].sudo().get_param(key, "") or ""

    def action_digiflazz_check_balance(self):
        """Read the Digiflazz deposit balance (cek-saldo).

        Exposed for reconciling our provider bucket against theirs. Read-only --
        it moves no money and creates no transaction.
        """
        self.ensure_one()
        if self.adapter_class != "ppob_digiflazz":
            raise UserError(_("Provider %s is not using the Digiflazz adapter.") % self.code)
        adapter = self._get_adapter()
        result = adapter.check_balance()
        if not result.ok:
            raise UserError(
                _("Digiflazz balance check failed: %s") % (result.error_message or result.error_code or "unknown error")
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "sticky": False,
                "title": _("Digiflazz Deposit"),
                "message": _("Balance: %s") % result.amount,
            },
        }
