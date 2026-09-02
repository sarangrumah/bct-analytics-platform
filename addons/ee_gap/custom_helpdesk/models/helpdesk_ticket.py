# -*- coding: utf-8 -*-
import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class HelpdeskTicket(models.Model):
    _name = "helpdesk.ticket"
    _description = "Helpdesk Ticket"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(
        string="Reference",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: self.env._("New"),
        tracking=True,
    )
    subject = fields.Char(required=True, tracking=True)
    team_id = fields.Many2one("helpdesk.team", string="Team", tracking=True)
    partner_id = fields.Many2one("res.partner", string="Customer", tracking=True)
    partner_email = fields.Char(related="partner_id.email", store=False, readonly=True)
    assignee_id = fields.Many2one(
        "res.users",
        string="Assigned To",
        tracking=True,
        default=lambda self: self.env.user,
    )
    priority = fields.Selection(
        [("0", "Low"), ("1", "Normal"), ("2", "High"), ("3", "Urgent")],
        default="1",
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        [
            ("new", "New"),
            ("open", "Open"),
            ("pending", "Pending"),
            ("resolved", "Resolved"),
            ("closed", "Closed"),
        ],
        default="new",
        required=True,
        tracking=True,
    )
    description = fields.Html()
    tag_ids = fields.Many2many("helpdesk.tag", string="Tags")
    sla_id = fields.Many2one(
        "helpdesk.sla",
        compute="_compute_sla",
        store=True,
        readonly=True,
    )
    sla_deadline = fields.Datetime(compute="_compute_sla_deadline", store=True)
    sla_status = fields.Selection(
        [("ok", "On Track"), ("warn", "Approaching"), ("breach", "Breached"), ("done", "Done")],
        compute="_compute_sla_status",
        store=True,
    )
    ai_suggested_text = fields.Text(string="AI Suggested Response")
    resolved_date = fields.Datetime(tracking=True)
    color = fields.Integer()

    # ---------- defaults / sequence ----------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("helpdesk.ticket") or "HD/0001"
            # default priority from team if not explicit
            if vals.get("team_id") and not vals.get("priority"):
                team = self.env["helpdesk.team"].browse(vals["team_id"])
                if team.default_priority:
                    vals["priority"] = team.default_priority
        return super().create(vals_list)

    # ---------- SLA ----------

    @api.depends("priority", "team_id", "team_id.sla_id")
    def _compute_sla(self):
        Sla = self.env["helpdesk.sla"]
        for rec in self:
            sla = False
            # 1) team default sla matches priority
            if rec.team_id and rec.team_id.sla_id and rec.team_id.sla_id.priority == rec.priority:
                sla = rec.team_id.sla_id
            # 2) any active SLA matching priority
            if not sla:
                sla = Sla.search([("priority", "=", rec.priority), ("active", "=", True)], limit=1)
            rec.sla_id = sla or False

    @api.depends("sla_id", "create_date", "sla_id.time_resolve_hours")
    def _compute_sla_deadline(self):
        for rec in self:
            if rec.sla_id and rec.create_date:
                rec.sla_deadline = rec.create_date + timedelta(hours=rec.sla_id.time_resolve_hours)
            else:
                rec.sla_deadline = False

    @api.depends("sla_deadline", "state", "resolved_date")
    def _compute_sla_status(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.state in ("resolved", "closed"):
                rec.sla_status = "done"
                continue
            if not rec.sla_deadline:
                rec.sla_status = "ok"
                continue
            delta = (rec.sla_deadline - now).total_seconds() / 3600.0
            if delta <= 0:
                rec.sla_status = "breach"
            elif delta <= 1.0:
                rec.sla_status = "warn"
            else:
                rec.sla_status = "ok"

    # ---------- state transitions + audit ----------

    def write(self, vals):
        old_states = {}
        if "state" in vals:
            old_states = {r.id: r.state for r in self}
        if "state" in vals and vals.get("state") in ("resolved", "closed"):
            vals.setdefault("resolved_date", fields.Datetime.now())
        res = super().write(vals)
        if "state" in vals:
            for rec in self:
                old = old_states.get(rec.id)
                new = vals.get("state")
                if old != new:
                    rec._pdp_audit_state_change(old, new)
        return res

    def _pdp_audit_state_change(self, old, new):
        try:
            user = self.env.user
            payload = {"old": old, "new": new, "ref": self.name}
            self.env.cr.execute(
                """
                INSERT INTO pdp.audit_log (
                    actor_user_id, actor_login, tenant_db,
                    model_name, res_id, action,
                    field_changes, classification
                ) VALUES (%s, %s, %s, %s, %s, 'write', %s::jsonb, 'internal')
                """,
                (
                    user.id if user else None,
                    user.login if user else None,
                    self.env.cr.dbname,
                    self._name,
                    self.id,
                    json.dumps(payload),
                ),
            )
        except Exception as e:  # pragma: no cover
            _logger.warning("helpdesk audit log failed: %s", e)

    # ---------- workflow buttons ----------

    def action_set_open(self):
        self.write({"state": "open"})

    def action_set_pending(self):
        self.write({"state": "pending"})

    def action_set_resolved(self):
        self.write({"state": "resolved"})

    def action_set_closed(self):
        self.write({"state": "closed"})

    # ---------- AI ----------

    def _custom_ai_payload(self):
        self.ensure_one()
        return {
            "ticket_ref": self.name,
            "subject": self.subject,
            "priority": dict(self._fields["priority"].selection).get(self.priority),
            "state": self.state,
            "team": self.team_id.name or "",
            "description": (self.description or "")[:4000],
            "customer": self.partner_id.name or "",
            "tags": self.tag_ids.mapped("name"),
        }

    def action_ai_suggest_response(self):
        self.ensure_one()
        try:
            result = self.env["custom.ai"]._recommend(
                model="helpdesk.ticket",
                res_id=self.id,
                payload=self._custom_ai_payload(),
            )
        except Exception as e:
            _logger.error("AI suggest failed: %s", e)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("AI Unavailable"),
                    "message": str(e),
                    "type": "warning",
                },
            }
        text = result.get("response") or result.get("text") or result.get("summary") or json.dumps(result)[:1000]
        self.ai_suggested_text = text
        self.message_post(
            body=_("<b>AI Suggested Response</b><br/>%s") % text,
            author_id=self.env.ref("base.partner_root").id,
            subtype_xmlid="mail.mt_note",
        )
        return True

    # ---------- cron ----------

    @api.model
    def cron_check_sla(self):
        open_tix = self.search([("state", "in", ("new", "open", "pending"))])
        if open_tix:
            open_tix._compute_sla_status()
        return True
