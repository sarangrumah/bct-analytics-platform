# -*- coding: utf-8 -*-
"""Override the engagement push hook to go through the SAP bridge.

When an enabled ``finance_sap_bridge`` config exists, ``_finance_push_to_sap``
enqueues an async ``queue_job`` that calls the bridge; otherwise it falls back
to the base stub. Inheriting the *abstract* ``finance.document.mixin``
propagates the override to every concrete document model (cash advance,
realization, reimbursement, vendor invoice).
"""

from __future__ import annotations

import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)

SAP_BRIDGE = "finance_sap_bridge"


class FinanceDocumentSap(models.AbstractModel):
    _inherit = "finance.document.mixin"

    def _sap_bridge_config(self):
        return (
            self.env["custom.adapter.config"]
            .sudo()
            .search([("adapter_type", "=", SAP_BRIDGE), ("status", "!=", "disabled")], limit=1)
        )

    def _finance_push_to_sap(self):
        cfg = self._sap_bridge_config()
        if not cfg:
            # No bridge configured → keep the local stub behaviour.
            return super()._finance_push_to_sap()
        for rec in self:
            rec.write(
                {
                    "sap_sync_state": "queued",
                    "sap_pushed_at": fields.Datetime.now(),
                    "state": "pushed",
                }
            )
            if hasattr(rec, "with_delay"):
                rec.with_delay(description="Push %s to SAP" % rec.name)._job_push_to_sap()
            else:  # queue_job runner unavailable (e.g. tests) → run inline
                rec._job_push_to_sap()
        return True

    def _job_push_to_sap(self):
        """Async worker: push this document's payload to the SAP bridge."""
        self.ensure_one()
        cfg = self._sap_bridge_config()
        Log = self.env["finance.sync.log"].sudo()
        if not cfg:
            Log._record("push", "push", "skipped", "No bridge", res_model=self._name, res_id=self.id)
            return False
        adapter = cfg.get_adapter()
        resp = adapter.push_document(self._finance_sap_payload())
        if resp.ok:
            self.write({"sap_sync_state": "pushed"})
            # The bridge acks the SAP document number + status asynchronously via
            # the inbound webhook; here we only confirm the push succeeded.
            sap_no = (resp.data or {}).get("sap_document_no") if isinstance(resp.data, dict) else None
            if sap_no:
                self.write({"sap_document_no": sap_no, "sap_sync_state": "ack"})
            Log._record("push", "push", "ok", res_model=self._name, res_id=self.id, payload=resp.data)
        else:
            self.write({"sap_sync_state": "error"})
            self.message_post(body="SAP push failed: %s" % (resp.error or "unknown"))
            Log._record("push", "push", "error", message=resp.error, res_model=self._name, res_id=self.id)
        return True
