# -*- coding: utf-8 -*-
"""Bulk-run phone/NIK normalization on any model."""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.custom_dedup_rule import (
    _normalize_phone_id,
    _validate_nik,
    _is_valid_phone_id_format,
)

_logger = logging.getLogger(__name__)


class CustomDedupNormalizeWizard(models.TransientModel):
    _name = "custom.dedup.normalize.wizard"
    _description = "Dedup Normalize Wizard"

    target_model = fields.Char(
        string="Target Model",
        required=True,
        default="res.partner",
    )
    target_field = fields.Char(
        string="Target Field",
        required=True,
        default="phone",
    )
    action = fields.Selection(
        selection=[
            ("normalize_phone", "Normalize Indonesian Phone"),
            ("validate_nik", "Validate NIK"),
        ],
        string="Action",
        required=True,
        default="normalize_phone",
    )
    result_summary = fields.Text(
        string="Result",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Class-level convenience methods (called by tests/console)
    # ------------------------------------------------------------------

    @api.model
    def action_normalize_phones_id(self, model_name="res.partner", field_name="phone"):
        """Scan ``model_name`` and rewrite ``field_name`` to canonical +62 form
        for any record whose value is not already valid.
        """
        Model = self.env[model_name].sudo()
        if field_name not in Model._fields:
            raise UserError(_("Field %s not found on %s.") % (field_name, model_name))
        updated = 0
        records = Model.search([(field_name, "!=", False)])
        for rec in records:
            try:
                current = rec[field_name]
                if not current:
                    continue
                if _is_valid_phone_id_format(current):
                    continue
                new_val = _normalize_phone_id(current)
                if new_val and new_val != current:
                    rec.write({field_name: new_val})
                    updated += 1
            except Exception as exc:  # pragma: no cover
                _logger.warning("normalize_phones_id skip %s.%s: %s", model_name, rec.id, exc)
        return updated

    @api.model
    def action_validate_nik_all(self, model_name="res.partner", field_name="x_nik"):
        """Flag records whose NIK field is malformed via mail.activity."""
        Model = self.env[model_name].sudo()
        if field_name not in Model._fields:
            return 0
        Activity = self.env["mail.activity"]
        ActivityType = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        IrModel = self.env["ir.model"].search([("model", "=", model_name)], limit=1)
        flagged = 0
        records = Model.search([(field_name, "!=", False)])
        for rec in records:
            try:
                val = rec[field_name]
                if val and not _validate_nik(val):
                    if ActivityType and IrModel and hasattr(rec, "activity_schedule"):
                        rec.activity_schedule(
                            "mail.mail_activity_data_todo",
                            summary=_("Invalid NIK: %s") % val,
                            note=_("NIK does not match the 16-digit format."),
                        )
                    flagged += 1
            except Exception as exc:  # pragma: no cover
                _logger.warning("validate_nik skip %s.%s: %s", model_name, rec.id, exc)
        return flagged

    # ------------------------------------------------------------------
    # Wizard runner
    # ------------------------------------------------------------------

    def action_run(self):
        self.ensure_one()
        if self.target_model not in self.env:
            raise UserError(_("Model %s not available.") % self.target_model)
        if self.action == "normalize_phone":
            n = self.action_normalize_phones_id(self.target_model, self.target_field)
            self.result_summary = _("Normalized %d phone value(s).") % n
        elif self.action == "validate_nik":
            n = self.action_validate_nik_all(self.target_model, self.target_field)
            self.result_summary = _("Flagged %d invalid NIK value(s).") % n
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
