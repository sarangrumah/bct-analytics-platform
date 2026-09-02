# -*- coding: utf-8 -*-
"""Reusable mixin that pushes create/write/unlink events to pdp.audit_log."""

from __future__ import annotations

import json
import logging
from typing import Any

from odoo import api, models
from odoo.http import request

_logger = logging.getLogger(__name__)


class PdpAuditedMixin(models.AbstractModel):
    _name = "pdp.audited.mixin"
    _description = "PDP Audited Mixin"

    # ---------- public hook (override to customize) ----------

    def _pdp_audit_classification(self) -> str | None:
        """Top-level classification representing this record (best-effort)."""
        # Look at any field with x_pdp_classification_id set
        codes = (
            self.env["ir.model.fields"]
            .sudo()
            .search(
                [
                    ("model", "=", self._name),
                    ("x_pdp_classification_id", "!=", False),
                ]
            )
            .mapped("x_pdp_classification_id.code")
        )
        if not codes:
            return None
        # Prefer most-sensitive code if available
        priority = ("sensitive_pii", "health", "financial", "pii", "confidential", "internal", "public")
        for p in priority:
            if p in codes:
                return p
        return codes[0]

    # ---------- internals ----------

    @api.model
    def _pdp_audit_write(self, action: str, res_id: int | None, field_changes: dict | None, reason: str | None = None):
        try:
            user = self.env.user
            classif = self._pdp_audit_classification()
            ip = None
            ua = None
            req_id = None
            try:
                if request:
                    ip = request.httprequest.environ.get("REMOTE_ADDR")
                    ua = request.httprequest.environ.get("HTTP_USER_AGENT")
                    req_id = request.httprequest.environ.get("HTTP_X_REQUEST_ID")
            except Exception:
                pass
            # Wrap the raw INSERT in a savepoint. Without it, a failed INSERT
            # (missing pdp schema on a cloned DB, action CHECK violation, bad
            # ::inet cast, append-only trigger error, ...) leaves the *whole*
            # request transaction aborted. The except below would swallow the
            # Python exception but every subsequent ORM query in the same
            # request would then die with InFailedSqlTransaction. The savepoint
            # confines the failure to this one statement so the business write
            # (e.g. account.move posting) can proceed.
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute(
                    """
                    INSERT INTO pdp.audit_log (
                        actor_user_id, actor_login, tenant_db,
                        model_name, res_id, action,
                        field_changes, classification,
                        ip_address, user_agent, request_id, reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::inet, %s, %s, %s)
                    """,
                    (
                        user.id if user else None,
                        user.login if user else None,
                        self.env.cr.dbname,
                        self._name,
                        res_id,
                        action,
                        json.dumps(field_changes or {}, default=str) if field_changes is not None else None,
                        classif,
                        ip,
                        ua,
                        req_id,
                        reason,
                    ),
                )
        except Exception as e:  # pragma: no cover - never block business write
            _logger.error("pdp.audit_log INSERT failed for %s/%s action=%s: %s", self._name, res_id, action, e)

    # ---------- ORM overrides ----------

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec, vals in zip(records, vals_list, strict=False):
            rec._pdp_audit_write("create", rec.id, _sanitize_vals(vals))
        return records

    def write(self, vals):
        # Pre-image is not captured to avoid PII duplication; only field-name keys are persisted.
        res = super().write(vals)
        sanitized = _sanitize_vals(vals)
        for rec in self:
            rec._pdp_audit_write("write", rec.id, sanitized)
        return res

    def unlink(self):
        for rec in self:
            rec._pdp_audit_write("unlink", rec.id, None)
        return super().unlink()


def _sanitize_vals(vals: dict[str, Any]) -> dict[str, Any]:
    """Strip binary/large blobs from audit payload; keep field names + lightweight repr."""
    out: dict[str, Any] = {}
    for k, v in (vals or {}).items():
        if isinstance(v, (bytes, bytearray, memoryview)):
            out[k] = f"<binary:{len(v)}b>"
        elif isinstance(v, str) and len(v) > 512:
            out[k] = v[:512] + "..."
        else:
            out[k] = v
    return out
