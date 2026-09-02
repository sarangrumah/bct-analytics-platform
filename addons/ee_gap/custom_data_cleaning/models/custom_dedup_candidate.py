# -*- coding: utf-8 -*-
import json

from odoo import _, fields, models
from odoo.exceptions import UserError


class CustomDedupCandidate(models.Model):
    _name = "custom.dedup.candidate"
    _description = "Custom Deduplication Candidate"
    _order = "created_at desc, id desc"

    rule_id = fields.Many2one(
        comodel_name="custom.dedup.rule",
        string="Rule",
        required=True,
        ondelete="cascade",
    )
    res_ids_json = fields.Text(
        string="Record IDs",
        help="JSON array of duplicate IDs",
    )
    preview = fields.Char(
        string="Preview",
    )
    match_key = fields.Char(
        string="Match Key",
        help="Normalized values used to group duplicates.",
    )
    state = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("merged", "Merged"),
            ("dismissed", "Dismissed"),
        ],
        string="State",
        default="pending",
        required=True,
    )
    created_at = fields.Datetime(
        string="Created At",
        default=fields.Datetime.now,
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_record_ids(self):
        self.ensure_one()
        try:
            ids = json.loads(self.res_ids_json or "[]")
        except (TypeError, ValueError):
            ids = []
        return [int(i) for i in ids]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_dismiss(self):
        for rec in self:
            rec.state = "dismissed"
        return True

    def action_open_merge_wizard(self):
        self.ensure_one()
        if self.state != "pending":
            raise UserError(_("Candidate is not pending."))
        ids = self._get_record_ids()
        if len(ids) < 2:
            raise UserError(_("Need at least 2 records to merge."))
        ctx = dict(self.env.context, default_candidate_id=self.id)
        return {
            "type": "ir.actions.act_window",
            "name": _("Merge Duplicates"),
            "res_model": "custom.dedup.merge.wizard",
            "view_mode": "form",
            "target": "new",
            "context": ctx,
        }
