# -*- coding: utf-8 -*-
"""Customer follow-up levels + cron escalator + email dispatcher."""

from __future__ import annotations

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class FollowupLevel(models.Model):
    _name = "custom.followup.level"
    _description = "Customer Follow-up Level"
    _order = "sequence, delay_days, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    delay_days = fields.Integer(
        string="Days Past Due",
        required=True,
        default=15,
    )
    send_email = fields.Boolean(default=True)
    email_template_id = fields.Many2one(
        "mail.template",
        domain="[('model', '=', 'res.partner')]",
    )
    action = fields.Selection(
        [
            ("reminder", "Friendly Reminder"),
            ("escalation", "Escalation"),
            ("legal", "Legal / Pre-litigation"),
        ],
        default="reminder",
        required=True,
    )

    @api.constrains("delay_days")
    def _check_delay_days(self):
        for level in self:
            if level.delay_days < 0:
                raise ValidationError(
                    _(
                        "Level '%(n)s' delay must be zero or positive.",
                        n=level.name,
                    )
                )

    @api.model
    def _cron_run_followup(self):
        """Spec-named cron entry; delegates to _cron_apply_followup."""
        return self._cron_apply_followup()

    @api.model
    def _cron_apply_followup(self):
        """Daily — push partners up the level ladder and send emails."""
        Partner = self.env["res.partner"]
        today = fields.Date.context_today(self)
        AML = self.env["account.move.line"].sudo()
        partner_ids = AML._read_group(
            domain=[
                ("account_id.account_type", "=", "asset_receivable"),
                ("parent_state", "=", "posted"),
                ("reconciled", "=", False),
                ("date_maturity", "<", today),
            ],
            groupby=["partner_id"],
        )
        partners = Partner.browse([p.id for (p,) in partner_ids if p])
        for partner in partners:
            partner._custom_advance_followup_level()
            partner._custom_send_followup_email_if_due()
        return True


class FollowupStatByPartner(models.Model):
    """SQL view: per-partner aggregated follow-up state."""

    _name = "custom.followup.stat.by.partner"
    _description = "Follow-up Statistics by Partner"
    _auto = False
    _order = "overdue_amount desc"

    partner_id = fields.Many2one("res.partner", readonly=True)
    partner_name = fields.Char(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    overdue_amount = fields.Float(readonly=True)
    open_invoice_count = fields.Integer(readonly=True)
    custom_followup_level_id = fields.Many2one(
        "custom.followup.level",
        readonly=True,
    )
    days_overdue = fields.Integer(readonly=True)

    def init(self):
        from odoo import tools

        # Skip view creation if the back-ref column on res_partner is not
        # yet materialised (happens on first install of this module, when
        # the SQL view init may run before the inherited field is created).
        self.env.cr.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name='res_partner' AND column_name='custom_followup_level_id'
        """)
        if not self.env.cr.fetchone():
            return
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT
                    p.id AS id,
                    p.id AS partner_id,
                    p.name AS partner_name,
                    p.company_id AS company_id,
                    p.custom_followup_level_id AS custom_followup_level_id,
                    COALESCE(MAX(
                        CASE
                            WHEN aml.date_maturity < CURRENT_DATE
                            THEN (CURRENT_DATE - aml.date_maturity)::int
                            ELSE 0
                        END
                    ), 0) AS days_overdue,
                    COALESCE(SUM(
                        CASE
                            WHEN aml.date_maturity < CURRENT_DATE
                            THEN aml.amount_residual
                            ELSE 0
                        END
                    ), 0) AS overdue_amount,
                    COUNT(DISTINCT am.id) FILTER (
                        WHERE am.payment_state IN ('not_paid', 'partial')
                    ) AS open_invoice_count
                FROM res_partner p
                LEFT JOIN account_move_line aml
                    ON aml.partner_id = p.id
                    AND aml.parent_state = 'posted'
                    AND aml.reconciled = false
                LEFT JOIN account_account aa
                    ON aa.id = aml.account_id
                    AND aa.account_type = 'asset_receivable'
                LEFT JOIN account_move am
                    ON am.id = aml.move_id
                    AND am.move_type IN ('out_invoice', 'out_refund')
                WHERE p.active = true
                GROUP BY p.id, p.name, p.company_id,
                         p.custom_followup_level_id
            )
        """)


class ResPartnerFollowup(models.Model):
    _inherit = "res.partner"

    custom_followup_level_id = fields.Many2one(
        "custom.followup.level",
        string="Current Follow-up Level",
        copy=False,
        tracking=True,
    )
    custom_followup_last_sent = fields.Datetime(
        readonly=True,
        copy=False,
    )
    custom_followup_next_date = fields.Date(
        copy=False,
        help="Earliest date the cron may re-send a reminder for this partner.",
    )
    custom_max_overdue_days = fields.Integer(
        compute="_compute_custom_max_overdue_days",
    )

    def _compute_custom_max_overdue_days(self):
        AML = self.env["account.move.line"]
        today = fields.Date.context_today(self)
        for partner in self:
            lines = AML.search(
                [
                    ("partner_id", "=", partner.id),
                    ("account_id.account_type", "=", "asset_receivable"),
                    ("parent_state", "=", "posted"),
                    ("reconciled", "=", False),
                    ("date_maturity", "<", today),
                ]
            )
            max_days = 0
            for line in lines:
                due = line.date_maturity or line.date
                if not due:
                    continue
                overdue = (today - due).days
                if overdue > max_days:
                    max_days = overdue
            partner.custom_max_overdue_days = max_days

    def _custom_advance_followup_level(self):
        Level = self.env["custom.followup.level"]
        for partner in self:
            company_id = partner.company_id.id if partner.company_id else self.env.company.id
            candidate = Level.search(
                [
                    ("active", "=", True),
                    ("company_id", "=", company_id),
                    ("delay_days", "<=", partner.custom_max_overdue_days),
                ],
                order="delay_days desc",
                limit=1,
            )
            if not candidate:
                continue
            current = partner.custom_followup_level_id
            if not current or candidate.sequence > current.sequence or candidate.delay_days > (current.delay_days or 0):
                partner.custom_followup_level_id = candidate

    def _custom_send_followup_email_if_due(self):
        today = fields.Date.context_today(self)
        for partner in self:
            level = partner.custom_followup_level_id
            if not level or not level.send_email or not level.email_template_id:
                continue
            if partner.custom_followup_next_date and partner.custom_followup_next_date > today:
                continue
            try:
                level.email_template_id.send_mail(
                    partner.id,
                    force_send=False,
                    raise_exception=False,
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning("followup email failed for %s: %s", partner.id, exc)
                continue
            partner.custom_followup_last_sent = fields.Datetime.now()
            throttle = max(7, int((level.delay_days or 15) / 2))
            partner.custom_followup_next_date = today + timedelta(days=throttle)
