# -*- coding: utf-8 -*-
"""The single outbound path Odoo → ESB.

Every document Odoo creates in ESB goes through this model, for one reason:
**ESB accepts no idempotency key on any POST**. A create that times out, or a
worker that dies between the HTTP call and the commit, would otherwise leave a
document in ESB that Odoo does not know about — and the retry would create a
second one. Duplicated item journals mean duplicated stock adjustments and
duplicated GL entries.

The guard: every outbox row generates an ``idempotency_key``, stamps it into the
document's free-text ``additionalInfo`` field, and **searches the matching Index
endpoint for that key before creating**. If a document already carries the key,
its number is adopted instead of posting again.

Two things this depends on, both worth confirming with the ESB PIC:
``additionalInfo`` is stored verbatim, and the Index endpoints filter on it.
Until confirmed, ``_find_existing`` degrades safely: it returns "not found" only
when the lookup itself succeeded, and a *failed* lookup aborts the push rather
than risking a duplicate.
"""

from __future__ import annotations

import logging
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .esb_adapter import ESB_CORE, EsbApiError

_logger = logging.getLogger(__name__)

DOC_TYPES = [
    ("item_journal", "Item Journal (Stock Adjustment)"),
    ("purchase_request", "Purchase Request"),
    ("goods_transfer_request", "Goods Transfer Request"),
    ("purchase_order", "Purchase Order"),
]

STATES = [
    ("draft", "Draft"),
    ("queued", "Queued"),
    ("sent", "Sent"),
    ("confirmed", "Authorized in ESB"),
    ("failed", "Failed"),
    ("cancelled", "Cancelled"),
]

#: doc_type -> (create path, index path, result key holding the document number)
DOC_SPEC = {
    "item_journal": ("inventory/item-journal", "inventory/item-journal", "itemJournalNum"),
    "purchase_request": ("purchase/purchase-request", "purchase/purchase-request", "purchaseRequestNum"),
    "goods_transfer_request": ("inventory/goods-transfer-request", "inventory/goods-transfer-request", "transferNum"),
    "purchase_order": ("purchase/purchase-order", "purchase/purchase-order", "purchaseNum"),
}

#: Only the item journal has a documented authorize verb we drive automatically;
#: the purchasing documents keep their approval flow inside ESB by design.
AUTHORIZABLE = {"item_journal"}

MAX_ATTEMPTS = 5


class EsbOutbox(models.Model):
    _name = "custom.esb.outbox"
    _description = "ESB Outbound Document"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "create_date desc"

    name = fields.Char(compute="_compute_name", store=True)
    doc_type = fields.Selection(DOC_TYPES, required=True, index=True, tracking=True)
    state = fields.Selection(STATES, default="draft", required=True, index=True, tracking=True)
    payload = fields.Json(required=True, help="The exact body POSTed to ESB.")
    idempotency_key = fields.Char(required=True, index=True, copy=False, readonly=True)
    esb_doc_num = fields.Char(string="ESB Document No.", index=True, copy=False, readonly=True, tracking=True)
    esb_status_id = fields.Integer(readonly=True, help="ESB statusID: 1 New, 2 Rejected, 3 Authorized, 38 Waiting.")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, index=True)

    res_model = fields.Char(index=True, help="Odoo record that produced this document.")
    res_id = fields.Integer(index=True)

    attempt_count = fields.Integer(default=0, readonly=True)
    last_error = fields.Char(readonly=True)
    sent_at = fields.Datetime(readonly=True)
    adopted = fields.Boolean(
        readonly=True,
        help="Set when the idempotency guard found the document already existed in ESB "
        "and adopted it instead of creating a duplicate.",
    )

    _idempotency_uniq = models.Constraint("unique(idempotency_key)", "Outbox idempotency key must be unique.")

    @api.depends("doc_type", "esb_doc_num", "idempotency_key")
    def _compute_name(self):
        for rec in self:
            label = dict(DOC_TYPES).get(rec.doc_type, rec.doc_type or "")
            rec.name = rec.esb_doc_num or f"{label} ({(rec.idempotency_key or '')[:8]})"

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault("idempotency_key", self._new_key())
            payload = vals.get("payload")
            if isinstance(payload, dict):
                vals["payload"] = self._stamp_key(payload, vals["idempotency_key"])
        return super().create(vals_list)

    @api.model
    def _new_key(self) -> str:
        # Prefixed so a human staring at additionalInfo in the ESB UI can tell
        # where it came from.
        return "ODOO-%s" % uuid.uuid4().hex[:20]

    @staticmethod
    def _stamp_key(payload: dict, key: str) -> dict:
        payload = dict(payload)
        existing = (payload.get("additionalInfo") or "").strip()
        payload["additionalInfo"] = f"{existing} [{key}]".strip() if existing else key
        return payload

    @api.model
    def enqueue(self, doc_type, payload, res_model=None, res_id=None):
        """Create an outbox row and schedule the push."""
        if doc_type not in DOC_SPEC:
            raise UserError(_("Unknown ESB document type '%s'.") % doc_type)
        rec = self.create({"doc_type": doc_type, "payload": payload, "res_model": res_model, "res_id": res_id})
        rec.action_queue()
        return rec

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def action_queue(self):
        for rec in self.filtered(lambda r: r.state in ("draft", "failed")):
            rec.state = "queued"
            rec.with_delay(description="ESB push %s" % rec.display_name)._job_push()
        return True

    def action_push_now(self):
        """Synchronous push — used from the UI and by tests."""
        for rec in self:
            rec._push()
        return True

    def action_reset_to_draft(self):
        for rec in self.filtered(lambda r: r.state == "failed"):
            rec.write({"state": "draft", "last_error": False})
        return True

    def action_cancel(self):
        for rec in self.filtered(lambda r: r.state in ("draft", "queued", "failed")):
            rec.state = "cancelled"
        return True

    def _job_push(self):
        self.ensure_one()
        return self._push()

    # ------------------------------------------------------------------
    # The push itself
    # ------------------------------------------------------------------

    def _push(self):
        self.ensure_one()
        if self.state in ("sent", "confirmed", "cancelled"):
            return True
        sync = self.env["custom.esb.master.sync"]
        if not sync._enabled("esb.push_enabled"):
            self.write({"state": "queued", "last_error": "esb.push_enabled is off"})
            _logger.info("ESB push suppressed for %s: esb.push_enabled is off", self.display_name)
            return False
        adapter = sync._adapter(ESB_CORE)
        if adapter is None:
            self.write({"state": "queued", "last_error": "No active esb_core adapter config"})
            return False

        create_path, index_path, num_key = DOC_SPEC[self.doc_type]
        self.attempt_count += 1

        # Idempotency guard: has a previous attempt already landed this document?
        try:
            existing_num = self._find_existing(adapter, index_path, num_key)
        except EsbApiError as exc:
            # Lookup failed — we cannot tell whether the document exists, so do
            # NOT post. Posting blind is how duplicates get created.
            return self._fail(_("Idempotency lookup failed, push aborted: %s") % exc)
        if existing_num:
            _logger.info("ESB %s already exists as %s — adopting", self.display_name, existing_num)
            self.write({"esb_doc_num": existing_num, "state": "sent", "adopted": True, "last_error": False})
            self._post_send(adapter)
            return True

        resp = adapter.call(create_path, payload=self.payload, method="POST")
        if not resp.ok:
            return self._fail(resp.error or _("ESB rejected the document"))
        result = (resp.data or {}).get("result") or {}
        doc_num = result.get(num_key) if isinstance(result, dict) else None
        if not doc_num:
            return self._fail(_("ESB accepted the document but returned no %s") % num_key)
        self.write({"esb_doc_num": doc_num, "state": "sent", "sent_at": fields.Datetime.now(), "last_error": False})
        self.env["custom.esb.sync.log"]._record(
            "push",
            self.doc_type,
            "ok",
            message=doc_num,
            res_model=self.res_model,
            res_id=self.res_id,
            payload=self.payload,
        )
        self._post_send(adapter)
        return True

    def _find_existing(self, adapter, index_path, num_key):
        """Look up a previously-created document by our idempotency key.

        Raises ``EsbApiError`` if the lookup could not be performed — the caller
        must treat that as "unknown", not "absent".
        """
        self.ensure_one()
        rows = adapter.get_rows(index_path, {"additionalInfo": self.idempotency_key, "limit": 20})
        for row in rows:
            # ESB filters additionalInfo as a substring, so confirm the exact key
            # is present rather than trusting the filter.
            if self.idempotency_key in (row.get("additionalInfo") or ""):
                return row.get(num_key)
        return None

    def _post_send(self, adapter):
        """Authorize the document when ESB and configuration both allow it."""
        self.ensure_one()
        if self.doc_type not in AUTHORIZABLE or not self.esb_doc_num:
            return False
        sync = self.env["custom.esb.master.sync"]
        if not sync._enabled("esb.auto_authorize_item_journal"):
            return False
        create_path, _index, _num = DOC_SPEC[self.doc_type]
        resp = adapter.call(f"{create_path}/{self.esb_doc_num}/authorize", payload=None, method="PATCH")
        if not resp.ok:
            # The document exists and is valid; it just is not approved yet.
            # Not a failure of the push — leave it "sent" for a human to finish.
            self.write({"last_error": _("Created, but authorize failed: %s") % resp.error})
            self.message_post(body=_("ESB authorize failed: %s") % resp.error)
            return False
        self.write({"state": "confirmed", "esb_status_id": 3, "last_error": False})
        return True

    def _fail(self, message):
        self.ensure_one()
        message = (message or "")[:255]
        state = "failed" if self.attempt_count >= MAX_ATTEMPTS else "queued"
        self.write({"state": state, "last_error": message})
        self.env["custom.esb.sync.log"]._record(
            "push", self.doc_type, "error", message=message, res_model=self.res_model, res_id=self.res_id
        )
        _logger.warning("ESB push %s failed (attempt %s): %s", self.display_name, self.attempt_count, message)
        return False

    # ------------------------------------------------------------------
    # Status reconciliation
    # ------------------------------------------------------------------

    @api.model
    def _cron_refresh_status(self):
        """Pull back the ESB status of documents we sent but did not authorize."""
        sync = self.env["custom.esb.master.sync"]
        adapter = sync._adapter(ESB_CORE)
        if adapter is None:
            return False
        pending = self.sudo().search([("state", "=", "sent"), ("esb_doc_num", "!=", False)], limit=200)
        for rec in pending:
            _create, index_path, _num = DOC_SPEC[rec.doc_type]
            try:
                rows = adapter.get_rows(f"{index_path}/{rec.esb_doc_num}")
            except EsbApiError as exc:
                _logger.info("ESB status refresh failed for %s: %s", rec.esb_doc_num, exc)
                continue
            if not rows:
                continue
            status_id = rows[0].get("statusID")
            if not status_id:
                continue
            vals = {"esb_status_id": status_id}
            if status_id == 3:
                vals["state"] = "confirmed"
            elif status_id == 2:
                vals["state"] = "failed"
                vals["last_error"] = _("Rejected in ESB")
            rec.write(vals)
        return True
