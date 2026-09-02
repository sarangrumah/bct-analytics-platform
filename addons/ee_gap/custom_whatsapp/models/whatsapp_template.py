# -*- coding: utf-8 -*-
"""WhatsApp message templates (Meta-approved or local draft)."""

from __future__ import annotations

import logging
import re

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Matches {{1}}, {{2}}, ... placeholders used by Meta template bodies.
_VAR_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")

# Meta WABA template lifecycle -> our status enum.
_META_STATUS_MAP = {
    "APPROVED": "approved",
    "REJECTED": "rejected",
    "PENDING": "pending_review",
    "IN_APPEAL": "pending_review",
    "PENDING_DELETION": "pending_review",
    "DELETED": "rejected",
    "DISABLED": "rejected",
    "PAUSED": "pending_review",
}


class WhatsappTemplate(models.Model):
    _name = "whatsapp.template"
    _description = "WhatsApp Template"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(required=True)
    account_id = fields.Many2one(
        "whatsapp.account",
        string="Account",
        required=True,
        ondelete="cascade",
        index=True,
    )
    language_code = fields.Char(
        string="Language Code",
        default="id",
        help="BCP-47 language code as accepted by Meta (e.g. 'id', 'en_US').",
    )
    category = fields.Selection(
        [
            ("marketing", "Marketing"),
            ("utility", "Utility"),
            ("authentication", "Authentication"),
        ],
        default="utility",
    )
    body_text = fields.Text(
        required=True,
        help="Template body. Use {{1}}, {{2}}, ... placeholders for variables.",
    )
    status = fields.Selection(
        [
            ("draft", "Draft"),
            ("pending_review", "Pending Review"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    meta_template_id = fields.Char(
        string="Meta Template ID",
        readonly=True,
        help="Upstream template identifier assigned by Meta after approval.",
    )
    variables_count = fields.Integer(
        compute="_compute_variables_count",
        store=True,
    )

    @api.depends("body_text")
    def _compute_variables_count(self):
        for rec in self:
            if not rec.body_text:
                rec.variables_count = 0
                continue
            # Distinct positional variables, not raw match count.
            rec.variables_count = len({m.group(1) for m in _VAR_RE.finditer(rec.body_text)})

    # ----- Cron: poll Meta for template approval state -----

    @api.model
    def cron_poll_template_status(self):
        """Poll Meta WABA for the current status of pending templates.

        For each template in ``pending_review`` with a populated
        ``meta_template_id``, GET ``/{waba_id}/message_templates?name={name}``
        and update ``status`` from the matching entry. Sandbox accounts
        are skipped (log only).
        """
        pending = self.sudo().search(
            [
                ("status", "=", "pending_review"),
                ("meta_template_id", "!=", False),
            ]
        )
        for tpl in pending:
            account = tpl.account_id
            if not account or not account.is_active:
                continue
            if account.sandbox_mode:
                _logger.info(
                    "[whatsapp template poll] account=%s template=%s sandbox -> skip",
                    account.name,
                    tpl.name,
                )
                continue
            try:
                url = account._get_waba_url("message_templates")
                body = account._get(url, params={"name": tpl.name})
                entries = body.get("data") or []
                # Match by language too — Meta returns one row per (name, language).
                match = next(
                    (
                        e
                        for e in entries
                        if (e.get("language") or "").lower() == (tpl.language_code or "").lower()
                        or not tpl.language_code
                    ),
                    None,
                )
                if not match and entries:
                    match = entries[0]
                if not match:
                    continue
                upstream = (match.get("status") or "").upper()
                new_status = _META_STATUS_MAP.get(upstream)
                if new_status and new_status != tpl.status:
                    tpl.write({"status": new_status})
                    _logger.info(
                        "[whatsapp template poll] %s -> %s (%s)",
                        tpl.name,
                        new_status,
                        upstream,
                    )
            except Exception as e:
                _logger.warning(
                    "[whatsapp template poll] failed for %s: %s",
                    tpl.name,
                    e,
                )
