# -*- coding: utf-8 -*-
"""Read-only Odoo model over the master DB ``tenant_registry.action_log_v`` view.

The Odoo runtime database is *not* the master DB (super-admin lives in
``master_admin``), so we cannot use ``_auto = False`` to map directly to
the view. Instead we mirror the data via cron, like ``tenant.registry``,
into an append-only local model — preserving the source-of-truth chain
on the master side.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class TenantActionLog(models.Model):
    _name = "tenant.action.log"
    _description = "Tenant Registry Action Log (mirror)"
    _order = "ts desc"

    master_id = fields.Integer(required=True, index=True, help="ID in master DB action_log")
    ts = fields.Datetime(required=True)
    tenant_slug = fields.Char(index=True)
    action = fields.Char(required=True, index=True)
    actor = fields.Char(required=True)
    detail = fields.Json()
    outcome = fields.Selection(
        [("success", "Success"), ("failure", "Failure"), ("partial", "Partial")],
        required=True,
    )
    error = fields.Text()
    prev_hash_hex = fields.Char(string="Prev Hash")
    hash_hex = fields.Char(string="Hash")

    _master_id_uniq = models.Constraint(
        "unique(master_id)",
        "Master log id must be unique in mirror.",
    )

    @api.model
    def _cron_sync(self) -> None:
        """Mirror is monotonic — we only need rows with id > max(master_id)."""
        # Direct SQL into the *same* postgres cluster via custom_super_admin's
        # privileged connection. The Odoo runtime user must have been GRANTed
        # ``tenant_registry_reader`` (handled by 04-tenant-registry-schema.sql).
        max_known = 0
        latest = self.search([], order="master_id desc", limit=1)
        if latest:
            max_known = latest.master_id

        # Use raw connection to query master DB across schemas — same cluster.
        cr = self.env.cr
        # Determine if action_log_v is reachable from this DB. tenant_registry
        # is created in the *master* DB only; in tenant DBs the schema does
        # not exist, so attempting to query it raises ``UndefinedTable``.
        cr.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = 'tenant_registry'")
        if not cr.fetchone():
            _logger.debug("tenant.action.log._cron_sync: tenant_registry schema not visible from this DB; skipping")
            return

        cr.execute(
            """
            SELECT id, ts, tenant_slug, action, actor, detail, outcome, error,
                   prev_hash_hex, hash_hex
              FROM tenant_registry.action_log_v
             WHERE id > %s
          ORDER BY id ASC
             LIMIT 5000
            """,
            (max_known,),
        )
        rows = cr.dictfetchall()
        if not rows:
            return

        to_create: list[dict[str, Any]] = []
        for r in rows:
            to_create.append(
                {
                    "master_id": r["id"],
                    "ts": r["ts"].replace(tzinfo=None) if isinstance(r["ts"], datetime) else r["ts"],
                    "tenant_slug": r["tenant_slug"],
                    "action": r["action"],
                    "actor": r["actor"],
                    "detail": r["detail"] or {},
                    "outcome": r["outcome"],
                    "error": r["error"],
                    "prev_hash_hex": r["prev_hash_hex"],
                    "hash_hex": r["hash_hex"],
                }
            )
        if to_create:
            self.sudo().create(to_create)
            _logger.info("tenant.action.log: synced %s new rows", len(to_create))

    @api.model
    def action_verify_chain(self) -> dict:
        """Call master-side ``tenant_registry.verify_action_chain()`` and return result."""
        cr = self.env.cr
        try:
            cr.execute("SELECT * FROM tenant_registry.verify_action_chain()")
            rows = cr.dictfetchall()
        except Exception as e:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Chain verification failed"),
                    "message": str(e),
                    "type": "danger",
                    "sticky": True,
                },
            }
        if not rows:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Chain intact"),
                    "message": _("All action_log hashes verify."),
                    "type": "success",
                },
            }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Chain BROKEN"),
                "message": _("%s broken row(s) detected. See server log for details.") % len(rows),
                "type": "danger",
                "sticky": True,
            },
        }
