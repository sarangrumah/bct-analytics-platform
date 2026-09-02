# -*- coding: utf-8 -*-
"""The outbox: one queue, every origin, HMAC-signed hand-off to the BFF."""

import hashlib
import hmac
import json
import logging
import time

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BACKOFF_MINUTES = {1: 1, 2: 5, 3: 15, 4: 60, 5: 240}

PARAM_BFF_URL = "custom_project_notify.bff_url"
PARAM_SECRET = "custom_core.secure_endpoint.pmo.secret"
PARAM_TIMEOUT = "custom_project_notify.timeout_seconds"
PARAM_ENABLED = "custom_project_notify.dispatch_enabled"


class CustomProjectNotifyOutbox(models.Model):
    _name = "custom.project.notify.outbox"
    _description = "VAS Notification Outbox"
    _order = "create_date, id"

    event = fields.Char(required=True, index=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    res_label = fields.Char()
    vertical_id = fields.Many2one("custom.project.vertical", index=True)
    payload_json = fields.Text(required=True)

    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("sent", "Sent"),
            ("failed", "Failed"),
            ("skipped", "Skipped"),
        ],
        default="pending",
        required=True,
        index=True,
    )
    attempt = fields.Integer(default=0)
    next_retry_at = fields.Datetime()
    error = fields.Char()
    sent_at = fields.Datetime()
    log_ids = fields.One2many("custom.project.notify.log", "outbox_id")

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    @api.model
    def enqueue(self, record, event, recipients, extra=None):
        """Queue one notification. ``recipients`` is a list of resolved dicts."""
        if not recipients:
            # Nothing to send is still worth recording: "nobody had a number" is a
            # finding, not a non-event.
            self.env["custom.project.notify.log"].create(
                {
                    "event": event,
                    "res_model": record._name,
                    "res_id": record.id,
                    "res_label": record.display_name,
                    "channel": "odoo",
                    "success": False,
                    "skipped_reason": _("No recipient could be resolved for this rule set"),
                }
            )
            return self.browse()

        payload = {
            "event": event,
            "model": record._name,
            "id": record.id,
            "label": record.display_name,
            "url": self._record_url(record),
            "recipients": recipients,
            "context": extra or {},
            "tenant": self.env.cr.dbname,
        }
        vertical = getattr(record, "custom_vertical_id", None) or getattr(record, "vertical_id", None)
        if vertical:
            payload["vertical"] = {
                "code": vertical.code,
                "name": vertical.name,
                "label": vertical.label_for_message(),
            }
        return self.create(
            {
                "event": event,
                "res_model": record._name,
                "res_id": record.id,
                "res_label": record.display_name,
                "vertical_id": vertical.id if vertical else False,
                "payload_json": json.dumps(payload, default=str),
            }
        )

    @api.model
    def _record_url(self, record):
        base = (
            self.env["ir.config_parameter"].sudo().get_param("custom_project_notify.public_base_url")
            or self.env["ir.config_parameter"].sudo().get_param("web.base.url")
            or ""
        )
        base = base.rstrip("/")
        if record._name == "project.task":
            return f"{base}/tasks/{record.id}"
        if record._name == "custom.change.request":
            return f"{base}/cr/{record.id}"
        if record._name == "custom.weekly.progress":
            return f"{base}/weekly/{record.id}"
        if record._name == "project.project":
            return f"{base}/projects/{record.id}"
        return base

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    @api.model
    def cron_dispatch(self, limit=100):
        """Drain the outbox. Never raises: a cron that dies stops all later rows."""
        params = self.env["ir.config_parameter"].sudo()
        if params.get_param(PARAM_ENABLED, "1") not in ("1", "True", "true"):
            return
        now = fields.Datetime.now()
        rows = self.search(
            [
                ("state", "=", "pending"),
                "|",
                ("next_retry_at", "=", False),
                ("next_retry_at", "<=", now),
            ],
            limit=limit,
        )
        for row in rows:
            try:
                row._dispatch_one()
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the queue
                _logger.exception("VAS PMO: outbox %s crashed", row.id)
                row._mark_failed(str(exc))

    def _dispatch_one(self):
        self.ensure_one()
        params = self.env["ir.config_parameter"].sudo()
        url = (params.get_param(PARAM_BFF_URL) or "").rstrip("/")
        secret = params.get_param(PARAM_SECRET) or ""
        timeout = int(params.get_param(PARAM_TIMEOUT) or 15)

        if not url or not secret:
            # Not configured yet. Leave it pending rather than burning retries -- the
            # queue should survive a BFF that is not deployed yet.
            self.write({"error": _("BFF URL or HMAC secret not configured")})
            return

        import requests  # local import: keeps module import cheap

        body = self.payload_json.encode("utf-8")
        timestamp = str(int(time.time()))
        signature = hmac.new(
            secret.encode("utf-8"),
            timestamp.encode("ascii") + body,
            hashlib.sha256,
        ).hexdigest()

        response = requests.post(
            f"{url}/api/notify",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Timestamp": timestamp,
                "X-Signature": signature,
            },
            timeout=timeout,
        )
        if response.status_code >= 400:
            self._mark_failed(f"HTTP {response.status_code}: {response.text[:200]}")
            return

        self._record_delivery(response)
        self.write(
            {
                "state": "sent",
                "attempt": self.attempt + 1,
                "sent_at": fields.Datetime.now(),
                "error": False,
            }
        )

    def _record_delivery(self, response):
        """Mirror the BFF's per-channel result into the delivery log."""
        self.ensure_one()
        log_model = self.env["custom.project.notify.log"]
        try:
            data = response.json()
        except ValueError:
            data = {}
        results = data.get("results") or []
        payload = json.loads(self.payload_json)

        if not results:
            # BFF accepted but told us nothing: still record the hand-off.
            log_model.create(
                {
                    "outbox_id": self.id,
                    "event": self.event,
                    "res_model": self.res_model,
                    "res_id": self.res_id,
                    "res_label": self.res_label,
                    "vertical_id": self.vertical_id.id,
                    "channel": "wa",
                    "transport": "bff",
                    "success": True,
                    "attempt": self.attempt + 1,
                }
            )
            return

        for item in results:
            log_model.create(
                {
                    "outbox_id": self.id,
                    "event": self.event,
                    "res_model": self.res_model,
                    "res_id": self.res_id,
                    "res_label": self.res_label,
                    "vertical_id": self.vertical_id.id,
                    "channel": item.get("channel") or "wa",
                    "transport": item.get("transport"),
                    "recipient_kind": item.get("kind"),
                    "recipient_name": item.get("name"),
                    "recipient_email": item.get("email"),
                    "recipient_phone_masked": log_model.mask_phone(item.get("phone")),
                    "subject": (payload.get("label") or "")[:200],
                    "success": bool(item.get("success")),
                    "skipped_reason": item.get("skipped"),
                    "error_message": (item.get("error") or "")[:200] or False,
                    "attempt": self.attempt + 1,
                }
            )

    def _mark_failed(self, error):
        self.ensure_one()
        attempt = self.attempt + 1
        if attempt >= MAX_ATTEMPTS:
            self.write({"state": "failed", "attempt": attempt, "error": error[:200]})
            _logger.error(
                "VAS PMO: giving up on outbox %s after %s attempts: %s",
                self.id,
                attempt,
                error,
            )
            return
        delay = BACKOFF_MINUTES.get(attempt, 240)
        self.write(
            {
                "attempt": attempt,
                "error": error[:200],
                "next_retry_at": fields.Datetime.add(fields.Datetime.now(), minutes=delay),
            }
        )

    def action_retry(self):
        """Operator button on a failed row."""
        self.write({"state": "pending", "next_retry_at": False, "error": False})
        return True
