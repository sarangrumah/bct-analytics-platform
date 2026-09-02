# -*- coding: utf-8 -*-
"""Mirror of master-DB ``tenant_registry.backups`` for UI listing + restore action."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from croniter import croniter  # type: ignore

    _HAS_CRONITER = True
except ImportError:  # pragma: no cover
    croniter = None  # type: ignore
    _HAS_CRONITER = False


class TenantBackup(models.Model):
    _name = "tenant.backup"
    _description = "Tenant Backup Ledger (mirror)"
    _order = "started_at desc"

    master_id = fields.Integer(index=True, required=True)
    tenant_id = fields.Many2one("tenant.registry", ondelete="cascade", index=True)
    tenant_slug = fields.Char(index=True, required=True)
    kind = fields.Selection(
        [("manual", "Manual"), ("daily", "Daily"), ("monthly", "Monthly"), ("yearly", "Yearly")],
        required=True,
    )
    started_at = fields.Datetime(required=True)
    finished_at = fields.Datetime()
    size_bytes = fields.Integer()
    size_human = fields.Char(compute="_compute_size_human")
    s3_key = fields.Char()
    checksum_sha256 = fields.Char()
    outcome = fields.Selection(
        [("pending", "Pending"), ("success", "Success"), ("failure", "Failure")],
        required=True,
    )
    error = fields.Text()
    expires_at = fields.Datetime()

    _master_id_uniq = models.Constraint(
        "unique(master_id)",
        "Master backup id must be unique in mirror.",
    )

    @api.depends("size_bytes")
    def _compute_size_human(self):
        for rec in self:
            n = rec.size_bytes or 0
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if n < 1024:
                    rec.size_human = f"{n:.1f} {unit}"
                    break
                n /= 1024.0
            else:
                rec.size_human = f"{n:.1f} PB"

    # ------------------------------------------------------------------
    # Sync (orchestrator API for active tenants)
    # ------------------------------------------------------------------

    @api.model
    def _cron_sync_all(self) -> None:
        for tenant in (
            self.env["tenant.registry"]
            .sudo()
            .search(
                [
                    ("state", "in", ("active", "suspended")),
                ]
            )
        ):
            self._cron_sync_for(tenant.slug)

    @api.model
    def _cron_sync_for(self, slug: str) -> None:
        try:
            rows = self.env["custom.super.admin.orchestrator.client"].sudo().list_backups(slug)
        except Exception as e:
            _logger.warning("tenant.backup.sync_failed slug=%s err=%s", slug, e)
            return
        tenant = self.env["tenant.registry"].sudo().search([("slug", "=", slug)], limit=1)
        existing = {b.master_id: b for b in self.sudo().search([("tenant_slug", "=", slug)])}
        for r in rows:
            mid = r["id"]
            vals = {
                "master_id": mid,
                "tenant_id": tenant.id if tenant else False,
                "tenant_slug": slug,
                "kind": r["kind"],
                "started_at": self._to_dt(r["started_at"]),
                "finished_at": self._to_dt(r.get("finished_at")),
                "size_bytes": r.get("size_bytes") or 0,
                "s3_key": r.get("s3_key"),
                "checksum_sha256": r.get("checksum_sha256"),
                "outcome": r["outcome"],
                "error": r.get("error"),
                "expires_at": self._to_dt(r.get("expires_at")),
            }
            if mid in existing:
                existing[mid].sudo().write(vals)
            else:
                self.sudo().create(vals)

    @staticmethod
    def _to_dt(value):
        if not value:
            return False
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Scheduled backups (Track D)
    # ------------------------------------------------------------------

    @api.model
    def _cron_scheduled_backup(self) -> None:
        """Iterate active tenants and trigger backups whose cron is due.

        Runs every 15 minutes via ir.cron. For each active tenant with a
        ``backup_schedule`` cron expression, evaluate against
        ``last_scheduled_backup_at`` and trigger the orchestrator if due.
        """
        now = fields.Datetime.now()
        tenants = (
            self.env["tenant.registry"]
            .sudo()
            .search(
                [
                    ("state", "=", "active"),
                    ("backup_schedule", "!=", False),
                ]
            )
        )
        client = self.env["custom.super.admin.orchestrator.client"].sudo()
        for tenant in tenants:
            schedule = (tenant.backup_schedule or "").strip()
            if not schedule:
                continue
            base = tenant.last_scheduled_backup_at or (now - timedelta(days=1))
            try:
                due = self._cron_is_due(schedule, base, now)
            except Exception as e:  # bad cron expression — log on tenant and skip
                _logger.warning(
                    "tenant.backup.cron_parse_failed slug=%s expr=%r err=%s",
                    tenant.slug,
                    schedule,
                    e,
                )
                tenant.message_post(
                    body=_("Invalid backup_schedule cron expression %r: %s") % (schedule, e),
                )
                continue
            if not due:
                continue
            try:
                result = client.run_backup(tenant.slug, kind="daily")
                tenant.sudo().write({"last_scheduled_backup_at": now})
                self._cron_sync_for(tenant.slug)
                _logger.info(
                    "tenant.backup.scheduled.ok slug=%s key=%s",
                    tenant.slug,
                    (result or {}).get("s3_key"),
                )
            except Exception as e:
                _logger.exception("tenant.backup.scheduled.failed slug=%s", tenant.slug)
                try:
                    tenant.message_post(
                        body=_("Scheduled backup failed: %s") % e,
                    )
                except Exception:  # noqa: BLE001 — never let logging break the loop
                    pass

    @api.model
    def _cron_enforce_retention(self) -> None:
        """Ask the orchestrator to prune backups older than retention_days per tenant."""
        tenants = (
            self.env["tenant.registry"]
            .sudo()
            .search(
                [
                    ("state", "in", ("active", "suspended")),
                    ("backup_retention_days", ">", 0),
                ]
            )
        )
        client = self.env["custom.super.admin.orchestrator.client"].sudo()
        for tenant in tenants:
            try:
                client.enforce_backup_retention(tenant.slug, tenant.backup_retention_days)
                self._cron_sync_for(tenant.slug)
            except Exception as e:
                _logger.warning(
                    "tenant.backup.retention.failed slug=%s err=%s",
                    tenant.slug,
                    e,
                )
                try:
                    tenant.message_post(
                        body=_("Retention enforcement failed: %s") % e,
                    )
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _cron_is_due(expr: str, base: datetime, now: datetime) -> bool:
        """Return True if a cron expression fires between ``base`` (exclusive) and ``now`` (inclusive)."""
        if _HAS_CRONITER:
            it = croniter(expr, base)
            nxt = it.get_next(datetime)
            return nxt <= now
        # Minimal fallback for "min hour dom mon dow" with '*' or single integer fields.
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError(f"Unsupported cron expression: {expr!r}")
        minute, hour, dom, mon, dow = parts

        def _match(field: str, value: int) -> bool:
            if field == "*":
                return True
            if field.startswith("*/"):
                try:
                    step = int(field[2:])
                    return step > 0 and value % step == 0
                except ValueError:
                    return False
            try:
                return int(field) == value
            except ValueError:
                return False

        # Walk minute by minute from base+1min to now (cap iterations for safety).
        cur = base.replace(second=0, microsecond=0) + timedelta(minutes=1)
        ceiling = now.replace(second=0, microsecond=0)
        steps = 0
        while cur <= ceiling and steps < 24 * 60 * 31:  # at most ~1 month of minutes
            if (
                _match(minute, cur.minute)
                and _match(hour, cur.hour)
                and _match(dom, cur.day)
                and _match(mon, cur.month)
                and _match(dow, cur.weekday() + 1 if cur.weekday() < 6 else 0)
            ):
                return True
            cur += timedelta(minutes=1)
            steps += 1
        return False

    # ------------------------------------------------------------------
    # Restore action
    # ------------------------------------------------------------------

    def action_restore_to_staging(self):
        self.ensure_one()
        if self.outcome != "success" or not self.s3_key:
            raise UserError(_("Cannot restore: backup did not complete successfully."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Restore Backup"),
            "res_model": "tenant.restore.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_tenant_id": self.tenant_id.id,
                "default_slug": self.tenant_slug,
                "default_s3_key": self.s3_key,
            },
        }
