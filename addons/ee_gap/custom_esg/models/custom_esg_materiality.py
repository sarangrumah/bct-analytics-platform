# -*- coding: utf-8 -*-
"""Materiality assessment: stakeholder × business impact quadrant.

Used by sustainability teams to score each ESG topic on two axes and
plot it on a 2x2 matrix:

                Critical     |  Important
                (high SH,    | (high SH,
                 low biz)    |  high biz)
                -------------+-------------
                Monitoring   |  Minor
                (low SH,     | (low SH,
                 low biz)    |  high biz)

(POJK 51 §3 lists materiality as a mandatory disclosure axis.)
"""

from __future__ import annotations

from odoo import api, fields, models


class CustomEsgMateriality(models.Model):
    _name = "custom.esg.materiality"
    _description = "ESG Materiality Assessment"
    _order = "stakeholder_importance desc, business_impact desc"

    name = fields.Char(
        string="Assessment Label",
        compute="_compute_name",
        store=True,
    )
    topic_id = fields.Many2one(
        comodel_name="custom.esg.metric",
        string="ESG Topic / Metric",
        required=True,
        ondelete="cascade",
    )
    stakeholder_importance = fields.Integer(
        string="Stakeholder Importance (1-10)",
        required=True,
        default=5,
    )
    business_impact = fields.Integer(
        string="Business Impact (1-10)",
        required=True,
        default=5,
    )
    quadrant = fields.Selection(
        [
            ("critical", "Critical (high SH / low biz)"),
            ("important", "Important (high SH / high biz)"),
            ("minor", "Minor (low SH / high biz)"),
            ("monitoring", "Monitoring (low SH / low biz)"),
        ],
        string="Quadrant",
        compute="_compute_quadrant",
        store=True,
    )
    assessment_year = fields.Integer(
        string="Assessment Year",
        default=lambda self: fields.Date.context_today(self).year,
    )
    notes = fields.Text(string="Notes")
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        default=lambda self: self.env.company,
    )

    _topic_year_company_uniq = models.Constraint(
        "unique(topic_id, assessment_year, company_id)",
        "A topic can only be assessed once per year per company.",
    )
    _sh_range_chk = models.Constraint(
        "CHECK(stakeholder_importance >= 1 AND stakeholder_importance <= 10)",
        "Stakeholder importance must be between 1 and 10.",
    )
    _bi_range_chk = models.Constraint(
        "CHECK(business_impact >= 1 AND business_impact <= 10)",
        "Business impact must be between 1 and 10.",
    )

    @api.depends("topic_id", "assessment_year")
    def _compute_name(self):
        for rec in self:
            rec.name = "%s [%s]" % (
                rec.topic_id.name or "?",
                rec.assessment_year or "",
            )

    @api.depends("stakeholder_importance", "business_impact")
    def _compute_quadrant(self):
        for rec in self:
            sh_high = (rec.stakeholder_importance or 0) >= 6
            bi_high = (rec.business_impact or 0) >= 6
            if sh_high and bi_high:
                rec.quadrant = "important"
            elif sh_high and not bi_high:
                rec.quadrant = "critical"
            elif (not sh_high) and bi_high:
                rec.quadrant = "minor"
            else:
                rec.quadrant = "monitoring"
