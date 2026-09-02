# -*- coding: utf-8 -*-
from odoo import _, fields, models


class CustomFixedAssetPostWizard(models.TransientModel):
    _name = "custom.fixed.asset.post.wizard"
    _description = "Post Due Fixed Asset Depreciation"

    cutoff_date = fields.Date(
        string="Post Up To",
        required=True,
        default=fields.Date.context_today,
        help="Every running asset's depreciation lines dated on or before this date will be posted.",
    )
    group_id = fields.Many2one(
        comodel_name="custom.fixed.asset.group",
        string="Group",
        help="Optional: limit posting to a single asset group.",
    )
    location_id = fields.Many2one(
        comodel_name="custom.fixed.asset.location",
        string="Location",
        help="Optional: limit posting to a single location.",
    )
    company_ids = fields.Many2many(
        comodel_name="res.company",
        string="Companies",
        default=lambda self: self.env.companies,
    )

    def action_post(self):
        self.ensure_one()
        domain = [("state", "=", "running")]
        if self.company_ids:
            domain.append(("company_id", "in", self.company_ids.ids))
        if self.group_id:
            domain.append(("group_id", "=", self.group_id.id))
        if self.location_id:
            domain.append(("location_id", "=", self.location_id.id))
        assets = self.env["custom.fixed.asset"].search(domain)
        count = assets._post_due_depreciation(as_of=self.cutoff_date)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if count else "warning",
                "title": _("Depreciation Posting"),
                "message": _(
                    "%(count)s depreciation entr(y/ies) posted up to %(date)s.",
                    count=count,
                    date=self.cutoff_date,
                ),
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
