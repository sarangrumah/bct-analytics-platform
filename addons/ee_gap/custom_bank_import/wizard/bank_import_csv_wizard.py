# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import hashlib
import logging

from odoo import _, fields, models
from odoo.addons.account.models.account_move import BYPASS_LOCK_CHECK
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BankImportCsvWizard(models.TransientModel):
    _name = "custom.bank.import.csv.wizard"
    _description = "Bank Statement CSV Import Wizard"

    journal_id = fields.Many2one("account.journal", required=True, domain=[("type", "=", "bank")])
    template_id = fields.Many2one("custom.bank.import.template", required=True)
    file = fields.Binary(string="Statement File", required=True)
    filename = fields.Char()
    statement_name = fields.Char(default="Imported")

    def action_import(self):
        self.ensure_one()
        Log = self.env["custom.bank.import.log"].sudo()
        if not self.file:
            raise UserError(_("Please upload a file."))
        raw = base64.b64decode(self.file)
        file_hash = hashlib.sha256(raw).hexdigest()
        existing = Log.search(
            [
                ("file_hash", "=", file_hash),
                ("state", "in", ("imported", "partial")),
            ],
            limit=1,
        )
        if existing:
            raise UserError(
                _(
                    "This exact file was already imported (log #%s). "
                    "Archive the previous import first if you really want to redo.",
                )
                % existing.id
            )

        try:
            parsed = self.template_id.parse_csv(self.file)
        except Exception as e:  # pragma: no cover - defensive
            _logger.exception("CSV parse failed")
            log = Log.create(
                {
                    "template_id": self.template_id.id,
                    "journal_id": self.journal_id.id,
                    "filename": self.filename,
                    "file_hash": file_hash,
                    "state": "failed",
                    "error_message": str(e),
                }
            )
            raise UserError(_("Parsing failed: %s") % e) from e

        lines = parsed["lines"]
        errors = parsed["errors"]
        if not lines:
            log = Log.create(
                {
                    "template_id": self.template_id.id,
                    "journal_id": self.journal_id.id,
                    "filename": self.filename,
                    "file_hash": file_hash,
                    "state": "failed",
                    "line_count": 0,
                    "error_count": len(errors),
                    "error_message": "; ".join(f"row {n}: {e}" for n, e in errors[:50]) or "No parseable lines.",
                }
            )
            raise UserError(
                _(
                    "No transaction lines parsed. %s parse errors. See log #%s.",
                )
                % (len(errors), log.id)
            )

        # The accounting date must stay the bank's transaction date. A soft
        # fiscalyear lock would otherwise make core _post() silently shift
        # locked-period lines to today; the hard lock is non-negotiable.
        hard_lock = self.journal_id.company_id.hard_lock_date
        if hard_lock:
            violating = sorted({ln["date"] for ln in lines if ln["date"] <= hard_lock})
            if violating:
                raise UserError(
                    _(
                        "The file contains %(count)s transaction dates up to %(worst)s, "
                        "on or before the hard lock date (%(lock)s). These periods are "
                        "permanently closed and cannot be imported.",
                        count=len(violating),
                        worst=violating[-1],
                        lock=hard_lock,
                    )
                )

        Statement = self.env["account.bank.statement"]
        StatementLine = self.env["account.bank.statement.line"]
        statement = Statement.create(
            {
                "name": self.statement_name or self.filename or "Bank Import",
                "date": max(ln["date"] for ln in lines),
                "journal_id": self.journal_id.id,
            }
        )
        line_vals = []
        for ln in lines:
            line_vals.append(
                {
                    "statement_id": statement.id,
                    "journal_id": self.journal_id.id,
                    "date": ln["date"],
                    "payment_ref": (ln["ref"] or ln.get("partner_hint") or "/")[:255],
                    "ref": (ln["ref"] or "")[:64] or False,
                    "amount": float(ln["amount"]),
                }
            )
        st_lines = StatementLine.create(line_vals)

        # Restore transaction dates that _post() shifted out of soft-locked
        # periods. Grouped writes keep this cheap on multi-hundred-line files.
        shifted = {}
        for st_line, ln in zip(st_lines, lines, strict=False):
            if st_line.date != ln["date"]:
                shifted.setdefault(ln["date"], StatementLine.browse())
                shifted[ln["date"]] |= st_line
        for target_date, recs in shifted.items():
            recs.with_context(bypass_lock_check=BYPASS_LOCK_CHECK).write({"date": target_date})

        state = "partial" if errors else "imported"
        log = Log.create(
            {
                "template_id": self.template_id.id,
                "journal_id": self.journal_id.id,
                "statement_id": statement.id,
                "filename": self.filename,
                "file_hash": file_hash,
                "line_count": len(line_vals),
                "error_count": len(errors),
                "state": state,
                "raw_payload": "; ".join(f"row {n}: {e}" for n, e in errors[:200]) if errors else False,
            }
        )

        return {
            "type": "ir.actions.act_window",
            "res_model": "custom.bank.import.log",
            "res_id": log.id,
            "view_mode": "form",
            "target": "current",
        }
