# -*- coding: utf-8 -*-
from odoo import _, fields, models


class CustomSpreadsheetVersion(models.Model):
    _name = "custom.spreadsheet.version"
    _description = "Spreadsheet Workbook Version"
    _order = "workbook_id, version_no desc"

    workbook_id = fields.Many2one(
        "custom.spreadsheet.workbook",
        string="Workbook",
        required=True,
        ondelete="cascade",
        index=True,
    )
    version_no = fields.Integer(string="Version #", required=True)
    data_json_snapshot = fields.Text(string="Data Snapshot (JSON)", required=True)
    saved_by = fields.Many2one(
        "res.users",
        string="Saved By",
        default=lambda self: self.env.user,
        readonly=True,
    )
    saved_at = fields.Datetime(
        string="Saved At",
        default=fields.Datetime.now,
        readonly=True,
    )
    note = fields.Char(string="Note")

    _version_uniq = models.Constraint(
        "unique(workbook_id, version_no)",
        "Version number must be unique per workbook.",
    )

    def action_restore(self):
        self.ensure_one()
        self.workbook_id.with_context(spreadsheet_skip_versioning=True).write({"data_json": self.data_json_snapshot})
        self.workbook_id.message_post(
            body=_("<b>Restored to version #%(v)s</b> (saved by %(u)s on %(d)s)")
            % {
                "v": self.version_no,
                "u": self.saved_by.name or "",
                "d": fields.Datetime.to_string(self.saved_at) or "",
            },
            subtype_xmlid="mail.mt_note",
        )
        return True
