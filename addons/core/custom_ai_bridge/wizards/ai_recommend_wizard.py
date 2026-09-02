# -*- coding: utf-8 -*-
"""Generic "Ask AI" wizard — callable from any record's form view."""

from __future__ import annotations

import json

from odoo import _, fields, models
from odoo.exceptions import UserError


class AiRecommendWizard(models.TransientModel):
    _name = "custom.ai.recommend.wizard"
    _description = "Ask AI for a recommendation about the current record"

    model_name = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    locale = fields.Char(default="id_ID")
    summary = fields.Text(readonly=True)
    next_actions_text = fields.Text(readonly=True)
    priority = fields.Char(readonly=True)
    tags = fields.Char(readonly=True)
    raw_text = fields.Text(readonly=True)

    def action_ask(self):
        self.ensure_one()
        model = self.env.get(self.model_name)
        if model is None:
            raise UserError(_("Unknown model %s") % self.model_name)
        record = model.browse(self.res_id).exists()
        if not record:
            raise UserError(_("Record %s/%s not found") % (self.model_name, self.res_id))

        # Best-effort payload: fields known to the record minus heavy blobs
        payload = {}
        for fname, field in record._fields.items():
            if field.type in ("binary", "image"):
                continue
            try:
                val = record[fname]
                if hasattr(val, "_name"):  # recordset
                    payload[fname] = (val._name, val.ids[:5])
                else:
                    payload[fname] = val
            except Exception:
                continue

        result = self.env["custom.ai"]._recommend(
            model=self.model_name,
            res_id=self.res_id,
            payload=payload,
            locale=self.locale,
        )
        self.summary = result.get("summary") or ""
        self.next_actions_text = json.dumps(result.get("next_actions") or [], indent=2, ensure_ascii=False)
        self.priority = result.get("priority") or ""
        self.tags = ", ".join(result.get("tags") or [])
        self.raw_text = result.get("raw_text") or ""

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
