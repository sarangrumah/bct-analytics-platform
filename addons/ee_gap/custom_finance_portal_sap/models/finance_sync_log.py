# -*- coding: utf-8 -*-
"""Sync log + master-data sync runner for the SAP/HRIS bridge.

The runner is contract-first and idempotent: every external record carries a
stable ``x_sap_external_id`` used to upsert. When no enabled bridge config
exists, the runner logs ``skipped`` and returns — so the portal works before the
SAP/Kafka connectors are ready.
"""

from __future__ import annotations

import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

SAP_BRIDGE = "finance_sap_bridge"
HRIS_BRIDGE = "finance_hris_bridge"

# (feed kind, upsert handler method name). Kept declarative so adding a feed is
# a one-line change once the bridge exposes it.
SAP_MASTER_FEEDS = [
    ("division", "_upsert_divisions"),
    ("item_category", "_upsert_item_categories"),
    ("supplier", "_upsert_suppliers"),
    ("budget", "_upsert_budgets"),
]


class FinanceSyncLog(models.Model):
    _name = "finance.sync.log"
    _description = "Finance Portal Sync Log"
    _order = "create_date desc"

    direction = fields.Selection(
        [("push", "Portal → SAP"), ("pull", "SAP → Portal"), ("status", "Status In")],
        required=True,
        default="pull",
        index=True,
    )
    operation = fields.Char(required=True, index=True)
    res_model = fields.Char()
    res_id = fields.Integer()
    record_count = fields.Integer()
    status = fields.Selection(
        [("ok", "OK"), ("error", "Error"), ("skipped", "Skipped")],
        required=True,
        default="ok",
        index=True,
    )
    message = fields.Char()
    payload = fields.Text()

    # ------------------------------------------------------------------
    @api.model
    def _record(
        self, direction, operation, status, message=None, res_model=None, res_id=None, record_count=None, payload=None
    ):
        vals = {
            "direction": direction,
            "operation": operation,
            "status": status,
            "message": (message or "")[:255] or False,
            "res_model": res_model,
            "res_id": res_id,
            "record_count": record_count,
        }
        if payload is not None:
            try:
                vals["payload"] = json.dumps(payload, default=str)[:65535]
            except (TypeError, ValueError):
                vals["payload"] = str(payload)[:65535]
        return self.sudo().create(vals)

    # ------------------------------------------------------------------
    # Bridge resolution
    # ------------------------------------------------------------------
    @api.model
    def _bridge(self, adapter_type=SAP_BRIDGE):
        cfg = (
            self.env["custom.adapter.config"]
            .sudo()
            .search([("adapter_type", "=", adapter_type), ("status", "!=", "disabled")], limit=1)
        )
        return cfg.get_adapter() if cfg else None

    # ------------------------------------------------------------------
    # Cron entry points
    # ------------------------------------------------------------------
    @api.model
    def _cron_sync_sap_masters(self):
        adapter = self._bridge(SAP_BRIDGE)
        if adapter is None:
            self._record("pull", "master:sap", "skipped", "No enabled SAP bridge config")
            return False
        for kind, handler in SAP_MASTER_FEEDS:
            try:
                resp = adapter.get_master(kind)
            except Exception as e:  # pragma: no cover - network
                self._record("pull", "master:%s" % kind, "error", str(e))
                continue
            if not resp.ok:
                self._record("pull", "master:%s" % kind, "error", resp.error)
                continue
            records = (resp.data or {}).get("records", []) if isinstance(resp.data, dict) else []
            count = getattr(self, handler)(records)
            self._record("pull", "master:%s" % kind, "ok", record_count=count)
        return True

    @api.model
    def _cron_sync_hris_travel(self):
        adapter = self._bridge(HRIS_BRIDGE)
        if adapter is None:
            self._record("pull", "hris:travel", "skipped", "No enabled HRIS bridge config")
            return False
        try:
            resp = adapter.get_travel()
        except Exception as e:  # pragma: no cover - network
            self._record("pull", "hris:travel", "error", str(e))
            return False
        if not resp.ok:
            self._record("pull", "hris:travel", "error", resp.error)
            return False
        records = (resp.data or {}).get("records", []) if isinstance(resp.data, dict) else []
        count = self._upsert_travel(records)
        self._record("pull", "hris:travel", "ok", record_count=count)
        return True

    # ------------------------------------------------------------------
    # Idempotent upsert helpers (keyed by x_sap_external_id)
    # ------------------------------------------------------------------
    @api.model
    def _upsert(self, model, ext_id, vals):
        """Create or update one record by its SAP external id. Returns 1/0."""
        if not ext_id:
            return 0
        Model = self.env[model].sudo()
        rec = Model.search([("x_sap_external_id", "=", ext_id)], limit=1)
        if rec:
            rec.write(vals)
        else:
            Model.create({**vals, "x_sap_external_id": ext_id})
        return 1

    @api.model
    def _upsert_divisions(self, records):
        n = 0
        for r in records:
            n += self._upsert(
                "finance.vertical",
                r.get("id"),
                {
                    "name": r.get("name") or r.get("id"),
                    "code": r.get("code"),
                    "cost_center_code": r.get("cost_center"),
                },
            )
        return n

    @api.model
    def _upsert_item_categories(self, records):
        n = 0
        for r in records:
            n += self._upsert(
                "finance.item.submission",
                r.get("id"),
                {"name": r.get("name") or r.get("id"), "code": r.get("code")},
            )
        return n

    @api.model
    def _upsert_suppliers(self, records):
        Partner = self.env["res.partner"].sudo()
        n = 0
        for r in records:
            ext = r.get("id")
            if not ext:
                continue
            vals = {
                "name": r.get("name") or ext,
                "ref": r.get("code") or ext,
                "supplier_rank": 1,
                "is_company": True,
            }
            partner = Partner.search([("ref", "=", r.get("code") or ext)], limit=1)
            if partner:
                partner.write(vals)
            else:
                Partner.create(vals)
            n += 1
        return n

    @api.model
    def _upsert_budgets(self, records):
        n = 0
        for r in records:
            division = (
                self.env["finance.vertical"].sudo().search([("x_sap_external_id", "=", r.get("division_id"))], limit=1)
            )
            if not division:
                continue
            n += self._upsert(
                "finance.budget",
                r.get("id"),
                {
                    "name": r.get("name") or r.get("id"),
                    "division_id": division.id,
                    "cost_center_code": r.get("cost_center"),
                    "budget_year": int(r.get("year") or fields.Date.context_today(self).year),
                    "budget_amount": float(r.get("amount") or 0.0),
                },
            )
        return n

    @api.model
    def _upsert_travel(self, records):
        n = 0
        for r in records:
            ext = r.get("id")
            if not ext:
                continue
            emp = self.env["hr.employee"].sudo().search([("identification_id", "=", r.get("nik"))], limit=1)
            vals = {
                "name": r.get("reference") or ext,
                "requester_id": emp.id if emp else False,
                "destination": r.get("destination"),
                "purpose": r.get("purpose"),
                "travel_from": r.get("from"),
                "travel_to": r.get("to"),
                "hris_state": r.get("state"),
                "estimated_amount": float(r.get("estimated") or 0.0),
                "realized_amount": float(r.get("realized") or 0.0),
            }
            n += self._upsert("finance.travel.settlement", ext, vals)
        return n

    # ------------------------------------------------------------------
    # Inbound status mirror (called by the webhook controller)
    # ------------------------------------------------------------------
    @api.model
    def _apply_status_in(self, payload):
        """Apply a SAP status callback onto the referenced document."""
        model = payload.get("doc_model")
        ref = payload.get("doc_ref")
        if (
            model
            not in (
                "finance.cash.advance",
                "finance.cash.advance.realization",
                "finance.reimbursement",
                "finance.vendor.invoice",
            )
            or not ref
        ):
            self._record("status", "status_in", "error", "Bad doc_model/doc_ref", payload=payload)
            return False
        doc = self.env[model].sudo().search([("name", "=", ref)], limit=1)
        if not doc:
            self._record("status", "status_in", "error", "Document not found: %s" % ref, payload=payload)
            return False
        doc._finance_apply_sap_status(
            {
                "sap_document_no": payload.get("sap_document_no"),
                "sap_payment_plan_date": payload.get("sap_payment_plan_date"),
                "sap_payment_status": payload.get("sap_payment_status"),
            }
        )
        self._record("status", "status_in", "ok", res_model=model, res_id=doc.id, payload=payload)
        return True
