# -*- coding: utf-8 -*-
"""Append-only audit log with sha256 hash chain.

* ``write()`` and ``unlink()`` are disabled → raise UserError.
* ``hash`` = sha256(prev_hash + canonical-json of relevant fields), hex.
* Genesis row (seeded in data/audit_event_seed.xml) has prev_hash="".
* ``create()`` automatically resolves prev_hash from the latest existing
  row and computes ``hash``.
"""

from __future__ import annotations

import hashlib
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_EVENT_TYPES = [
    ("vertical_provision", "Vertical Provision"),
    ("vertical_suspend", "Vertical Suspend"),
    ("module_deploy", "Module Deploy"),
    ("module_upgrade", "Module Upgrade"),
    ("brd_approve", "BRD Approve"),
    ("incident_acknowledge", "Incident Acknowledge"),
    ("ai_config_change", "AI Config Change"),
    ("secret_rotate", "Secret Rotate"),
    ("genesis", "Genesis"),
]

# Fields included in the hash computation (deterministic order).
_HASH_FIELDS = (
    "timestamp",
    "user_id",
    "event_type",
    "tenant_id",
    "object_ref",
    "summary",
    "payload",
    "prev_hash",
)


class CustomHubAuditEvent(models.Model):
    _name = "custom.hub.audit.event"
    _description = "Hub Audit Event (append-only, hash-chained)"
    _order = "id asc"
    _rec_name = "summary"

    timestamp = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    user_id = fields.Many2one(
        "res.users",
        string="Actor",
        default=lambda self: self.env.user.id,
        index=True,
    )
    event_type = fields.Selection(_EVENT_TYPES, required=True, index=True)
    tenant_id = fields.Many2one(
        "tenant.registry",
        string="Tenant",
        ondelete="set null",
        index=True,
    )
    object_ref = fields.Reference(
        selection="_selection_object_ref",
        string="Related Object",
    )
    summary = fields.Char(required=True)
    payload = fields.Json()
    prev_hash = fields.Char(string="Previous Hash", default="")
    hash = fields.Char(string="Hash", index=True)

    # ------------------------------------------------------------------
    @api.model
    def _selection_object_ref(self):
        """Whitelist of models that audit events may reference."""
        candidates = [
            ("tenant.registry", "Tenant"),
            ("custom.hub.module.catalog", "Catalog Entry"),
            ("custom.hub.module.deployment", "Deployment"),
            ("res.users", "User"),
        ]
        # Filter to only models present in the registry (safer for tests).
        out = []
        for model, label in candidates:
            if model in self.env:
                out.append((model, label))
        return out

    # ------------------------------------------------------------------
    # Hash computation
    # ------------------------------------------------------------------
    @api.model
    def _canonical_payload(self, vals: dict) -> bytes:
        """Build deterministic canonical bytes from ``vals``.

        Only fields listed in ``_HASH_FIELDS`` participate in the digest.
        Datetimes are ISO-serialized; bytes are decoded; everything else
        falls back to ``str()`` via ``json.dumps(default=str)``.
        """
        doc = {}
        for fname in _HASH_FIELDS:
            v = vals.get(fname)
            if isinstance(v, (bytes, bytearray)):
                v = v.decode("utf-8", "replace")
            if hasattr(v, "isoformat"):
                v = v.isoformat()
            doc[fname] = v
        return json.dumps(doc, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")

    @api.model
    def _compute_hash(self, vals: dict) -> str:
        return hashlib.sha256(self._canonical_payload(vals)).hexdigest()

    # ------------------------------------------------------------------
    # Create (auto-hash) + lockdown
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        out = self.env[self._name]
        for vals in vals_list:
            # Resolve prev_hash from the latest existing row if not set.
            if not vals.get("prev_hash"):
                latest = self.sudo().search(
                    [],
                    order="id desc",
                    limit=1,
                )
                vals["prev_hash"] = latest.hash if latest else ""
            if not vals.get("timestamp"):
                vals["timestamp"] = fields.Datetime.now()
            if not vals.get("user_id"):
                vals["user_id"] = self.env.user.id
            vals["hash"] = self._compute_hash(vals)
            out |= super().create([vals])
        return out

    def write(self, vals):
        # Allow internal fields to be updated only during install (e.g. seeding).
        raise UserError(_("Hub audit events are append-only. Modification is forbidden — create a new event instead."))

    def unlink(self):
        raise UserError(
            _(
                "Hub audit events are append-only and cannot be deleted. "
                "If retention requires purging, do so via a controlled "
                "database-level archive procedure, not through the UI."
            )
        )

    # ------------------------------------------------------------------
    # Convenience API
    # ------------------------------------------------------------------
    @api.model
    def log(self, event_type, summary, payload=None, tenant_id=False, object_ref=False, user_id=False):
        """Convenience helper — used by other modules to add an event."""
        vals = {
            "event_type": event_type,
            "summary": summary,
            "payload": payload or {},
            "tenant_id": tenant_id or False,
            "object_ref": object_ref or False,
            "user_id": user_id or self.env.user.id,
        }
        return self.sudo().create(vals)

    @api.model
    def verify_chain(self) -> dict:
        """Re-walk the chain and confirm every hash is consistent."""
        rows = self.sudo().search([], order="id asc")
        prev = ""
        bad = []
        for r in rows:
            vals = {
                "timestamp": r.timestamp,
                "user_id": r.user_id.id if r.user_id else False,
                "event_type": r.event_type,
                "tenant_id": r.tenant_id.id if r.tenant_id else False,
                "object_ref": r.object_ref and (f"{r.object_ref._name},{r.object_ref.id}") or False,
                "summary": r.summary,
                "payload": r.payload,
                "prev_hash": prev,
            }
            expected = self._compute_hash(vals)
            if expected != r.hash:
                bad.append(r.id)
            prev = r.hash
        return {"checked": len(rows), "bad_ids": bad, "ok": not bad}
