# -*- coding: utf-8 -*-
import json
import logging
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _advance(date_from, interval, count):
    if interval == "daily":
        return date_from + timedelta(days=count)
    if interval == "weekly":
        return date_from + timedelta(weeks=count)
    if interval == "monthly":
        return date_from + relativedelta(months=count)
    if interval == "yearly":
        return date_from + relativedelta(years=count)
    return date_from


class SubscriptionContract(models.Model):
    _name = "subscription.contract"
    _description = "Subscription Contract"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(required=True, copy=False, default=lambda s: s.env._("New"), tracking=True)
    partner_id = fields.Many2one("res.partner", required=True, tracking=True)
    plan_id = fields.Many2one("subscription.plan", required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        default=lambda s: s.env.company,
        required=True,
    )
    currency_id = fields.Many2one(related="plan_id.currency_id", store=True, readonly=True)
    start_date = fields.Date(default=fields.Date.today, required=True, tracking=True)
    next_billing_date = fields.Date(tracking=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("paused", "Paused"),
            ("churned", "Churned"),
            ("closed", "Closed"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    payment_term_id = fields.Many2one("account.payment.term")
    invoice_ids = fields.One2many(
        "account.move",
        "x_custom_subscription_id",
        string="Invoices",
    )
    last_invoice_id = fields.Many2one(
        "account.move",
        compute="_compute_last_invoice",
        store=True,
    )
    invoice_count = fields.Integer(compute="_compute_metrics", store=True)
    mrr = fields.Monetary(
        string="MRR",
        currency_field="currency_id",
        compute="_compute_metrics",
        store=True,
    )
    lifetime_value = fields.Monetary(
        string="LTV",
        currency_field="currency_id",
        compute="_compute_metrics",
        store=True,
    )

    ai_churn_summary = fields.Text()
    ai_churn_priority = fields.Selection(
        [("info", "Info"), ("warn", "Warn"), ("critical", "Critical")],
        default="info",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("subscription.contract") or "SUB/0001"
        return super().create(vals_list)

    # ---------- metrics ----------

    @api.depends("invoice_ids", "invoice_ids.state", "invoice_ids.invoice_date")
    def _compute_last_invoice(self):
        for rec in self:
            inv = rec.invoice_ids.sorted(lambda r: r.invoice_date or fields.Date.today(), reverse=True)
            rec.last_invoice_id = inv[:1].id if inv else False

    @api.depends(
        "invoice_ids",
        "invoice_ids.payment_state",
        "invoice_ids.amount_total",
        "plan_id",
        "plan_id.price",
        "plan_id.recurring_interval",
        "plan_id.recurring_count",
    )
    def _compute_metrics(self):
        for rec in self:
            paid = rec.invoice_ids.filtered(lambda r: r.payment_state in ("paid", "in_payment"))
            rec.lifetime_value = sum(paid.mapped("amount_total"))
            rec.invoice_count = len(rec.invoice_ids)
            mrr = 0.0
            if rec.plan_id and rec.state == "active":
                interval = rec.plan_id.recurring_interval
                price = rec.plan_id.price
                count = rec.plan_id.recurring_count or 1
                if interval == "daily":
                    mrr = price / count * 30
                elif interval == "weekly":
                    mrr = price / count * (30.0 / 7.0)
                elif interval == "monthly":
                    mrr = price / count
                elif interval == "yearly":
                    mrr = price / count / 12.0
            rec.mrr = mrr

    # ---------- workflow ----------

    def action_activate(self):
        for rec in self:
            if rec.state not in ("draft", "paused"):
                continue
            start = rec.start_date or fields.Date.today()
            if rec.plan_id.trial_days and rec.state == "draft":
                rec.next_billing_date = start + timedelta(days=rec.plan_id.trial_days)
            else:
                rec.next_billing_date = _advance(start, rec.plan_id.recurring_interval, rec.plan_id.recurring_count)
            rec.state = "active"

    def action_pause(self):
        self.write({"state": "paused"})

    def action_churn(self):
        self.write({"state": "churned"})

    def action_close(self):
        self.write({"state": "closed"})

    def action_invoice_now(self):
        AccountMove = self.env["account.move"]
        for rec in self:
            if not rec.plan_id or not rec.plan_id.product_id:
                raise UserError(_("Plan or its product is not configured."))
            product = rec.plan_id.product_id
            move = AccountMove.create(
                {
                    "move_type": "out_invoice",
                    "partner_id": rec.partner_id.id,
                    "invoice_date": fields.Date.today(),
                    "currency_id": rec.currency_id.id,
                    "invoice_payment_term_id": rec.payment_term_id.id or False,
                    "x_custom_subscription_id": rec.id,
                    "invoice_line_ids": [
                        (
                            0,
                            0,
                            {
                                "product_id": product.id,
                                "name": "%s — %s" % (rec.plan_id.name, rec.name),
                                "quantity": 1.0,
                                "price_unit": rec.plan_id.price,
                            },
                        )
                    ],
                }
            )
            try:
                move.action_post()
            except Exception as e:
                _logger.warning("subscription %s invoice post failed: %s", rec.name, e)
            # advance schedule
            base = rec.next_billing_date or fields.Date.today()
            rec.next_billing_date = _advance(base, rec.plan_id.recurring_interval, rec.plan_id.recurring_count)
            rec.message_post(body=_("Invoice %s generated.") % move.name)
        return True

    @api.model
    def cron_generate_invoices(self):
        today = fields.Date.today()
        due = self.search(
            [
                ("state", "=", "active"),
                ("next_billing_date", "<=", today),
            ]
        )
        for rec in due:
            try:
                rec.action_invoice_now()
            except Exception as e:
                _logger.error("cron_generate_invoices failed for %s: %s", rec.name, e)
        return True

    # ---------- AI ----------

    def _custom_ai_payload(self):
        self.ensure_one()
        invs = self.invoice_ids.sorted(lambda r: r.invoice_date or fields.Date.today(), reverse=True)[:6]
        return {
            "contract_ref": self.name,
            "partner": self.partner_id.name or "",
            "plan": self.plan_id.name or "",
            "plan_interval": self.plan_id.recurring_interval,
            "mrr": float(self.mrr or 0),
            "ltv": float(self.lifetime_value or 0),
            "state": self.state,
            "start_date": self.start_date and self.start_date.isoformat(),
            "recent_invoices": [
                {
                    "name": i.name,
                    "date": i.invoice_date and i.invoice_date.isoformat(),
                    "amount": float(i.amount_total or 0),
                    "payment_state": i.payment_state,
                }
                for i in invs
            ],
        }

    def action_churn_predict(self):
        self.ensure_one()
        try:
            result = self.env["custom.ai"]._recommend(
                model="subscription.contract",
                res_id=self.id,
                payload=self._custom_ai_payload(),
            )
        except Exception as e:
            _logger.error("AI churn predict failed: %s", e)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("AI Unavailable"),
                    "message": str(e),
                    "type": "warning",
                },
            }
        summary = result.get("summary") or result.get("response") or json.dumps(result)[:1000]
        # Best-effort: derive priority from result['priority'] or heuristics
        prio = (result.get("priority") or "info").lower()
        if prio not in ("info", "warn", "critical"):
            prio = "info"
        self.write(
            {
                "ai_churn_summary": summary,
                "ai_churn_priority": prio,
            }
        )
        self.message_post(
            body=_("<b>Churn Prediction (%s)</b><br/>%s") % (prio, summary),
            subtype_xmlid="mail.mt_note",
        )
        return True


class AccountMove(models.Model):
    _inherit = "account.move"

    x_custom_subscription_id = fields.Many2one(
        "subscription.contract",
        string="Subscription",
        index=True,
        copy=False,
    )
