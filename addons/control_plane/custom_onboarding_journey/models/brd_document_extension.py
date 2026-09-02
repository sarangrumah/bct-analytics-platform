# -*- coding: utf-8 -*-
"""Declare the ``journey_id`` field on ``brd.document``.

The field lives here (not in custom_brd_analyzer) because Odoo refuses to
set up a Many2one whose comodel is missing at load time. brd_analyzer is
loaded before custom_onboarding_journey in the module graph, so declaring
``journey_id`` upstream would fail with ``unknown comodel_name
'onboarding.journey'``. Declaring it via ``_inherit`` here keeps the
forward reference valid.
"""

from odoo import fields, models


class BrdDocumentJourneyLink(models.Model):
    _inherit = "brd.document"

    journey_id = fields.Many2one(
        comodel_name="onboarding.journey",
        string="Onboarding Journey",
        ondelete="set null",
        index=True,
        help="Onboarding journey this BRD belongs to.",
    )

    def write(self, vals):
        # If a BRD is bound to a journey, transition the journey when the BRD
        # reaches ``analyzed`` (best-effort; silent failure if context blocks).
        result = super().write(vals)
        if vals.get("state") == "analyzed":
            for rec in self:
                if rec.journey_id and rec.journey_id.stage == "brd_uploaded":
                    try:
                        rec.journey_id.with_context(_transition_notes="Auto from BRD analysis").write(
                            {"stage": "brd_analyzed"}
                        )
                    except Exception:
                        # Don't block BRD workflow on journey-state issues.
                        pass
        return result
