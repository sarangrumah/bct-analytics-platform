# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from datetime import datetime

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class OpsIncident(models.Model):
    _name = "custom.ops.incident"
    _description = "Operations Incident"
    _order = "fired_at desc, id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(compute="_compute_name", store=True)
    tenant_id = fields.Many2one(
        "tenant.registry",
        ondelete="set null",
        index=True,
        tracking=True,
    )
    alert_name = fields.Char(required=True, index=True, tracking=True)
    severity = fields.Selection(
        [("info", "Info"), ("warning", "Warning"), ("critical", "Critical"), ("page", "Page")],
        default="warning",
        required=True,
        index=True,
        tracking=True,
    )
    fired_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True, tracking=True)
    resolved_at = fields.Datetime(tracking=True)
    summary = fields.Char(tracking=True)
    description = fields.Text()
    runbook_url = fields.Char()
    state = fields.Selection(
        [("firing", "Firing"), ("acknowledged", "Acknowledged"), ("resolved", "Resolved")],
        default="firing",
        required=True,
        index=True,
        tracking=True,
    )
    assigned_user_id = fields.Many2one("res.users", tracking=True)
    fingerprint = fields.Char(index=True, help="Alertmanager fingerprint, for upsert dedup.")
    raw_payload = fields.Text()

    _fingerprint_uniq = models.Constraint(
        "unique(fingerprint)",
        "Alertmanager fingerprint must be unique.",
    )

    @api.depends("alert_name", "tenant_id", "fired_at")
    def _compute_name(self):
        for rec in self:
            t = rec.tenant_id.slug or "global"
            rec.name = f"[{t}] {rec.alert_name or 'alert'}"

    # ------------------------------------------------------------------
    # Webhook ingest helpers
    # ------------------------------------------------------------------

    @api.model
    def ingest_alertmanager_payload(self, payload: dict) -> "OpsIncident":
        """Upsert incidents from a single Alertmanager webhook payload.

        Returns the recordset of incidents touched.
        """
        alerts = payload.get("alerts") or []
        touched = self.browse()
        for alert in alerts:
            inc = self._upsert_one_alert(alert)
            if inc:
                touched |= inc
        return touched

    def _upsert_one_alert(self, alert: dict) -> "OpsIncident | None":
        fp = alert.get("fingerprint") or ""
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        alert_name = labels.get("alertname") or "unknown"
        severity = (labels.get("severity") or "warning").lower()
        if severity not in ("info", "warning", "critical", "page"):
            severity = "warning"
        status = alert.get("status") or "firing"
        tenant_slug = labels.get("tenant") or labels.get("db")
        tenant = (
            self.env["tenant.registry"]
            .sudo()
            .search(
                [("slug", "=", tenant_slug)],
                limit=1,
            )
            if tenant_slug
            else self.env["tenant.registry"].browse()
        )

        existing = self.sudo().search([("fingerprint", "=", fp)], limit=1) if fp else self.browse()
        vals = {
            "alert_name": alert_name,
            "severity": severity,
            "tenant_id": tenant.id or False,
            "summary": annotations.get("summary") or alert_name,
            "description": annotations.get("description") or "",
            "runbook_url": annotations.get("runbook_url") or "",
            "fingerprint": fp or False,
            "raw_payload": str(alert)[:10000],
        }
        if status == "resolved":
            vals["state"] = "resolved"
            vals["resolved_at"] = self._parse_dt(alert.get("endsAt")) or fields.Datetime.now()
        else:
            vals.setdefault("fired_at", self._parse_dt(alert.get("startsAt")) or fields.Datetime.now())

        if existing:
            existing.sudo().write(vals)
            inc = existing
        else:
            if status == "resolved":
                # Don't create a new incident in resolved state.
                return None
            vals["fired_at"] = self._parse_dt(alert.get("startsAt")) or fields.Datetime.now()
            inc = self.sudo().create(vals)
            inc._schedule_ack_activity()
        return inc

    @staticmethod
    def _parse_dt(value):
        if not value:
            return False
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
        except (TypeError, ValueError):
            return False

    def _schedule_ack_activity(self) -> None:
        self.ensure_one()
        ops_group = self.env.ref(
            "custom_ops_monitor.group_ops_engineer",
            raise_if_not_found=False,
        )
        if not ops_group or not ops_group.user_ids:
            return
        # Round-robin would be nice; for now pick the first ops engineer.
        user = ops_group.user_ids[0]
        self.activity_schedule(
            "mail.mail_activity_data_todo",
            summary=f"Acknowledge: {self.alert_name}",
            note=self.description or self.summary or "",
            user_id=user.id,
        )

    # ------------------------------------------------------------------

    def action_acknowledge(self):
        for rec in self:
            if rec.state == "firing":
                rec.state = "acknowledged"
                rec.assigned_user_id = self.env.user
        return True

    def action_resolve(self):
        for rec in self:
            rec.state = "resolved"
            rec.resolved_at = fields.Datetime.now()
        return True
