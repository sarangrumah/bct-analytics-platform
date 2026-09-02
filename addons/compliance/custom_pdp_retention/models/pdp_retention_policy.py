# -*- coding: utf-8 -*-
"""Retention policies + cron applier."""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class PdpRetentionPolicy(models.Model):
    _name = "pdp.retention.policy"
    _description = "PDP Retention Policy"
    _order = "model_id, classification_id"
    _rec_name = "display_name"
    _inherit = ["pdp.audited.mixin"]

    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade")
    model_name = fields.Char(related="model_id.model", store=True, index=True)
    classification_id = fields.Many2one("pdp.classification", required=True, ondelete="restrict")
    retention_days = fields.Integer(required=True, default=1825)
    action = fields.Selection(
        [
            ("anonymize", "Anonymize"),
            ("archive", "Archive"),
            ("delete", "Delete"),
        ],
        required=True,
        default="anonymize",
    )
    date_field = fields.Char(
        default="create_date",
        help="Date field on the target model used to compute age (default: create_date).",
    )
    active = fields.Boolean(default=True)
    last_run = fields.Datetime(readonly=True)
    next_run = fields.Datetime(compute="_compute_next_run", store=True)
    records_eligible_count = fields.Integer(compute="_compute_eligible", store=False)
    display_name = fields.Char(compute="_compute_display_name", store=True)

    _policy_unique = models.Constraint(
        "unique(model_id, classification_id)",
        "Only one policy per model+classification.",
    )

    @api.depends("model_id", "classification_id")
    def _compute_display_name(self):
        for r in self:
            r.display_name = "%s / %s" % (
                r.model_id.model or "-",
                r.classification_id.code or "-",
            )

    @api.depends("last_run")
    def _compute_next_run(self):
        for r in self:
            base = r.last_run or fields.Datetime.now()
            r.next_run = base + timedelta(days=1)

    def _compute_eligible(self):
        for r in self:
            r.records_eligible_count = r._count_eligible()

    def _eligible_domain(self) -> list:
        cutoff = fields.Datetime.now() - timedelta(days=self.retention_days or 0)
        return [(self.date_field or "create_date", "<", cutoff)]

    def _count_eligible(self) -> int:
        try:
            if not self.model_id or self.model_id.model not in self.env:
                return 0
            return self.env[self.model_id.model].sudo().search_count(self._eligible_domain())
        except Exception as e:
            _logger.info("retention._count_eligible failed for %s: %s", self.model_name, e)
            return 0

    # ---------- cron entry point ----------

    @api.model
    def cron_apply_retention(self, limit_per_policy: int = 500):
        policies = self.sudo().search([("active", "=", True)])
        for pol in policies:
            try:
                pol._apply(limit=limit_per_policy)
            except Exception as e:
                _logger.exception("retention policy %s failed: %s", pol.display_name, e)

    def action_run_now(self):
        for p in self:
            p._apply(limit=2000)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "PDP Retention",
                "message": "Policies applied.",
                "type": "success",
            },
        }

    def _apply(self, limit: int = 500):
        self.ensure_one()
        if self.model_id.model not in self.env:
            _logger.info("retention: model %s not in registry, skip", self.model_name)
            return
        Model = self.env[self.model_id.model].sudo()
        recs = Model.search(self._eligible_domain(), limit=limit)
        if not recs:
            self.last_run = fields.Datetime.now()
            return

        affected = len(recs)
        if self.action == "delete":
            try:
                recs.unlink()
            except Exception as e:
                _logger.warning("retention delete failed for %s: %s", self.model_name, e)
                affected = 0
        elif self.action == "archive":
            if "active" in Model._fields:
                recs.write({"active": False})
            else:
                _logger.info("retention: model %s has no 'active' field, skipping archive", self.model_name)
                affected = 0
        elif self.action == "anonymize":
            affected = self._anonymize_records(recs)

        self.last_run = fields.Datetime.now()
        if affected:
            self._pdp_audit_write(
                "custom",
                self.id,
                {
                    "policy": self.display_name,
                    "model": self.model_name,
                    "action": self.action,
                    "affected": affected,
                },
                reason=f"Retention policy applied: {self.action} x{affected}",
            )

    def _anonymize_records(self, recs) -> int:
        """Overwrite PII fields of `recs` with hash placeholders. Does not unlink."""
        Model = recs._name
        Field = self.env["ir.model.fields"].sudo()
        pii_fields = Field.search(
            [
                ("model", "=", Model),
                ("x_pdp_classification_id", "=", self.classification_id.id),
            ]
        )
        if not pii_fields:
            return 0
        affected = 0
        for rec in recs:
            digest = hashlib.sha256(f"{Model}:{rec.id}".encode()).hexdigest()[:12]
            placeholder = f"ANON-{digest}"
            vals = {}
            for f in pii_fields:
                ftype = rec._fields.get(f.name)
                if not ftype:
                    continue
                if ftype.type in ("char", "text", "html"):
                    vals[f.name] = placeholder
                elif ftype.type == "binary":
                    vals[f.name] = False
            if vals:
                try:
                    rec.write(vals)
                    affected += 1
                except Exception as e:
                    _logger.info("retention anonymize %s/%s skipped: %s", Model, rec.id, e)
        return affected
