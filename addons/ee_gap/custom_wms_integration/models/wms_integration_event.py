# -*- coding: utf-8 -*-
"""Outbound event outbox: Odoo -> external WMS / host.

Why an outbox at all: the host is remote and may be down, slow, or behind a
tripped circuit breaker. A warehouse operator validating a delivery must never
see that. So business hooks only ever *write a row* (same transaction, cheap,
local); a cron drains the rows through the adapter afterwards. The hooks are
additionally wrapped in a savepoint + bare ``except`` so that even a programming
error in the payload builder cannot roll back — or poison — the picking
transaction.

Rows are append-only-ish: the payload and the source reference are immutable
once written; only the delivery bookkeeping (state / attempts / last_error /
acked_at) may change.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .wms_host_adapter import EVENT_TYPES, WMS_HOST, WMS_SAP_HOST

_logger = logging.getLogger(__name__)

STATES = [
    ("pending", "Pending"),
    ("sending", "Sending"),
    ("sent", "Sent"),
    ("acked", "Acknowledged"),
    ("failed", "Failed"),
]

#: After this many attempts a row stops being retried by the cron and waits for
#: a human to press "Retry".
MAX_ATTEMPTS = 8

#: Fields a user may still change after creation; everything else is frozen.
_MUTABLE_FIELDS = {
    "state",
    "attempts",
    "last_error",
    "acked_at",
    "sent_at",
    "external_ref",
}

#: mail.thread / activity bookkeeping the ORM writes on our behalf.
_MAIL_FIELDS = {
    "message_ids",
    "message_follower_ids",
    "message_main_attachment_id",
    "activity_ids",
    "website_message_ids",
}


class WmsIntegrationEvent(models.Model):
    _name = "wms.integration.event"
    _description = "WMS Integration Outbound Event"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "id asc"

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda self: self.env._("New"), index=True)
    event_type = fields.Selection(EVENT_TYPES, required=True, index=True, tracking=True)
    res_model = fields.Char(string="Source Model", index=True)
    res_id = fields.Integer(string="Source ID", index=True)
    payload = fields.Json(help="Exact body handed to the adapter.")
    state = fields.Selection(STATES, default="pending", required=True, index=True, tracking=True)
    attempts = fields.Integer(default=0, readonly=True)
    last_error = fields.Char(readonly=True)
    external_ref = fields.Char(
        index=True,
        copy=False,
        help="Host-side reference. Set by us for correlation, echoed back on /api/wms/ack.",
    )
    sent_at = fields.Datetime(readonly=True)
    acked_at = fields.Datetime(readonly=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, index=True, required=True)

    # ------------------------------------------------------------------
    # Creation / immutability
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("wms.integration.event") or "/"
            if not vals.get("external_ref"):
                vals["external_ref"] = vals["name"]
        return super().create(vals_list)

    def write(self, vals):
        frozen = set(vals) - _MUTABLE_FIELDS - _MAIL_FIELDS
        if frozen and not self.env.context.get("wms_integration_bypass_freeze"):
            raise UserError(
                _("wms.integration.event is append-only; %s cannot be changed after creation.")
                % ", ".join(sorted(frozen))
            )
        return super().write(vals)

    def unlink(self):
        if not self.env.is_superuser() and any(rec.state in ("sent", "acked") for rec in self):
            raise UserError(_("Delivered WMS integration events cannot be deleted."))
        return super().unlink()

    # ------------------------------------------------------------------
    # Enqueue API
    # ------------------------------------------------------------------

    @api.model
    def enqueue(self, event_type, record=None, payload=None, external_ref=None, company=None):
        """Create one outbox row. Raises on bad input — use ``_safe_enqueue``
        from anything running inside a business transaction."""
        if event_type not in dict(EVENT_TYPES):
            raise UserError(_("Unknown WMS event type '%s'.") % event_type)
        if not company and record is not None and record and "company_id" in record._fields:
            company = record.company_id
        vals = {
            "event_type": event_type,
            "payload": payload or {},
            "company_id": (company or self.env.company).id,
        }
        if record:
            vals.update({"res_model": record._name, "res_id": record.id})
        if external_ref:
            vals["external_ref"] = external_ref
        return self.sudo().create(vals)

    @api.model
    def _safe_enqueue(self, event_type, record=None, payload=None, external_ref=None, company=None):
        """Enqueue from a business hook. NEVER raises, NEVER poisons the cursor.

        The savepoint matters as much as the try/except: a failed INSERT leaves
        PostgreSQL in ``InFailedSqlTransaction`` and every later statement of the
        business transaction would die with it.
        """
        try:
            with self.env.cr.savepoint():
                return self.enqueue(
                    event_type, record=record, payload=payload, external_ref=external_ref, company=company
                )
        except Exception:  # noqa: BLE001 - deliberately swallowing everything
            _logger.exception(
                "WMS outbox enqueue failed (event_type=%s, record=%s#%s) — business transaction continues",
                event_type,
                record and record._name,
                record and record.id,
            )
            return self.browse()

    # ------------------------------------------------------------------
    # Adapter resolution
    # ------------------------------------------------------------------

    @api.model
    def _adapter_config(self, company=None):
        """The ``custom.adapter.config`` this tenant pushes WMS events through.

        Pinned by ``ir.config_parameter`` ``wms_integration.adapter_config``
        (the config *name*); otherwise the first active WMS-typed config.
        """
        Config = self.env["custom.adapter.config"].sudo()
        name = self.env["ir.config_parameter"].sudo().get_param("wms_integration.adapter_config", "")
        if name:
            cfg = Config.search([("name", "=", name)], limit=1)
            if cfg:
                return cfg
            _logger.warning("wms_integration.adapter_config=%s does not match any adapter config", name)
        return Config.search(
            [("adapter_type", "in", (WMS_HOST, WMS_SAP_HOST)), ("status", "=", "active")],
            limit=1,
        )

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    def action_send(self):
        """Synchronous push — used from the UI, the cron, and the tests."""
        for rec in self:
            rec._send()
        return True

    def action_retry(self):
        self.sudo().write({"state": "pending", "last_error": False})
        return True

    def action_mark_acked(self):
        self.sudo().write({"state": "acked", "acked_at": fields.Datetime.now()})
        return True

    def _send(self):
        self.ensure_one()
        if self.state in ("sent", "acked"):
            return True
        config = self._adapter_config(self.company_id)
        if not config:
            self.sudo().write({"last_error": _("No active WMS adapter config")})
            return False
        try:
            adapter = config.get_adapter()
        except Exception as exc:  # noqa: BLE001
            self.sudo().write({"last_error": str(exc)[:255]})
            _logger.warning("WMS outbox %s: adapter unavailable: %s", self.name, exc)
            return False

        self.sudo().write({"state": "sending", "attempts": self.attempts + 1})
        try:
            resp = adapter.push_event(self.event_type, self._envelope())
        except Exception as exc:  # noqa: BLE001 - includes CircuitBreakerOpenError
            # Breaker open or an unexpected client error: keep the row pending so
            # the next cron pass retries once the cooldown elapsed.
            self.sudo().write({"state": "pending", "last_error": str(exc)[:255]})
            _logger.info("WMS outbox %s deferred: %s", self.name, exc)
            return False

        if resp.ok:
            self.sudo().write({"state": "sent", "sent_at": fields.Datetime.now(), "last_error": False})
            return True
        error = (resp.error or _("host rejected the event"))[:255]
        state = "failed" if self.attempts >= MAX_ATTEMPTS else "pending"
        self.sudo().write({"state": state, "last_error": error})
        _logger.warning("WMS outbox %s push failed (attempt %s): %s", self.name, self.attempts, error)
        return False

    def _envelope(self):
        """What actually goes on the wire."""
        self.ensure_one()
        return {
            "event_id": self.name,
            "event_type": self.event_type,
            "external_ref": self.external_ref or self.name,
            "occurred_at": fields.Datetime.to_string(self.create_date or fields.Datetime.now()),
            "company": self.company_id.name,
            "source": {"model": self.res_model, "id": self.res_id},
            "data": self.payload or {},
        }

    # ------------------------------------------------------------------
    # Acknowledgement (inbound, from /api/wms/ack)
    # ------------------------------------------------------------------

    @api.model
    def _ack(self, external_ref, host_ref=None):
        """Mark the outbox row acknowledged. Returns the recordset (possibly empty)."""
        ref = (external_ref or "").strip()
        if not ref:
            return self.browse()
        rec = self.sudo().search(["|", ("external_ref", "=", ref), ("name", "=", ref)], limit=1)
        if not rec:
            return self.browse()
        vals = {"state": "acked", "acked_at": fields.Datetime.now(), "last_error": False}
        rec.write(vals)
        if host_ref:
            rec.message_post(body=_("Acknowledged by host, reference %s") % host_ref)
        return rec

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    @api.model
    def _cron_drain_outbox(self, limit=200):
        """Drain pending events oldest-first.

        Stops early once the adapter's circuit breaker is open: hammering a dead
        host with 200 calls per cron pass is exactly what the breaker exists to
        prevent.
        """
        rows = self.sudo().search(
            [("state", "in", ("pending", "sending")), ("attempts", "<", MAX_ATTEMPTS)],
            order="id asc",
            limit=limit,
        )
        if not rows:
            return 0
        sent = 0
        for row in rows:
            config = self._adapter_config(row.company_id)
            if config and config.status == "circuit_open":
                _logger.info("WMS outbox drain halted: adapter %s circuit is open", config.name)
                break
            if row._send():
                sent += 1
        _logger.info("WMS outbox drain: %s/%s events delivered", sent, len(rows))
        return sent

    # ------------------------------------------------------------------
    # Cycle-count bridge
    # ------------------------------------------------------------------

    def _register_hook(self):
        """Patch ``custom.cycle.count.adjustment.action_post`` when that module
        happens to be installed.

        We deliberately do NOT depend on ``custom_wms_cycle_count``: this module
        must install on tenants that do not run cycle counting. ``_register_hook``
        runs after the registry is built, which is the only point where we can
        ask "is that model here?" without a hard dependency.
        """
        res = super()._register_hook()
        Adjustment = self.env.registry.get("custom.cycle.count.adjustment")
        if Adjustment is None or getattr(Adjustment, "_wms_integration_patched", False):
            return res
        original_action_post = Adjustment.action_post

        def action_post(adjustments):
            result = original_action_post(adjustments)
            try:
                adjustments.env["wms.integration.event"]._enqueue_adjustments(adjustments)
            except Exception:  # noqa: BLE001 - never break variance posting
                _logger.exception("WMS outbox: cycle-count adjustment hook failed")
            return result

        Adjustment.action_post = action_post
        Adjustment._wms_integration_patched = True
        _logger.info("WMS integration: hooked custom.cycle.count.adjustment.action_post")
        return res

    @api.model
    def _enqueue_adjustments(self, adjustments):
        """Enqueue one ``stock_adjustment`` event per posted cycle-count variance."""
        Mapping = self.env["wms.integration.mapping"]
        for adj in adjustments:
            if not adj.posted:
                continue
            line = adj.line_id
            move = adj.stock_move_id
            if not line:
                continue
            company = move.company_id if move else self.env.company
            payload = {
                "session": line.session_id.name if line.session_id else None,
                "sku": Mapping._external_code_for(line.product_id),
                "location_code": Mapping._external_code_for(line.location_id),
                "expected_qty": line.expected_qty,
                "counted_qty": line.counted_qty,
                "variance_qty": line.variance_qty,
                "move_id": move.id if move else None,
                "reason": "cycle_count",
            }
            self._safe_enqueue("stock_adjustment", record=adj, payload=payload, company=company)
        return True
