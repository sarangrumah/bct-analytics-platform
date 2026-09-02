# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CustomSpreadsheetLoadWizard(models.TransientModel):
    _name = "custom.spreadsheet.load.wizard"
    _description = "Load Records into Spreadsheet Workbook"

    workbook_id = fields.Many2one(
        "custom.spreadsheet.workbook",
        string="Workbook",
        required=True,
    )
    model_id = fields.Many2one(
        "ir.model",
        string="Model",
        required=True,
        domain=[("transient", "=", False)],
    )
    model_name = fields.Char(
        string="Model Name",
        related="model_id.model",
        store=False,
        readonly=True,
    )
    domain = fields.Char(
        string="Domain",
        default="[]",
        help="Odoo domain literal, e.g. [('active','=',True)]",
    )
    fields_csv = fields.Char(
        string="Fields (comma separated)",
        required=True,
        default="id, display_name",
        help="Comma-separated list of field names on the target model.",
    )
    sheet_name = fields.Char(
        string="Sheet Name",
        required=True,
        default="Data",
    )
    mode = fields.Selection(
        [
            ("replace", "Replace sheet"),
            ("append", "Append to sheet"),
        ],
        string="Mode",
        default="replace",
        required=True,
    )

    @api.onchange("model_id")
    def _onchange_model(self):
        if not self.model_id:
            self.fields_csv = "id, display_name"

    def action_load(self):
        self.ensure_one()
        if not self.model_id:
            raise UserError(_("Please select a model."))
        self.workbook_id.action_load_from_model(
            model_name=self.model_id.model,
            domain=self.domain or "[]",
            fields_list=self.fields_csv,
            sheet_name=self.sheet_name or "Data",
            append=(self.mode == "append"),
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "custom.spreadsheet.workbook",
            "res_id": self.workbook_id.id,
            "view_mode": "form",
            "target": "current",
        }
