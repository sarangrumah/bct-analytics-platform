# -*- coding: utf-8 -*-
import io

from odoo import _, fields, models
from odoo.exceptions import UserError


class BatchPaymentFormat(models.Model):
    """Pluggable bank transfer-file layout (mirrors the
    ``custom.bank.import.template`` idea, in the export direction).

    ``render(batch)`` dispatches to ``_render_<code>()`` when such a method
    exists, else falls back to a generic delimited layout. Column specs for
    the corporate H2H portals (BCA MCM, Mandiri MCM, …) vary per contract —
    the seeds are a sane baseline, refine them against real sample files.
    """

    _name = "custom.batch.payment.format"
    _description = "Batch Payment Export Format"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True, help="Stable identifier, e.g. 'bca_mcm'.")
    bank_label = fields.Char(help="Bank/portal this layout targets (BCA MCM, Mandiri MCM…).")
    active = fields.Boolean(default=True)
    encoding = fields.Char(default="utf-8")
    delimiter = fields.Char(default=",", help="Column delimiter for delimited layouts.")
    date_format = fields.Char(default="%d/%m/%Y")
    file_extension = fields.Char(default="csv")
    include_header = fields.Boolean(default=True)
    corporate_id = fields.Char(help="Corporate/company code the bank portal expects, if any.")
    debit_account = fields.Char(help="Source (debit) account number at the bank, if the layout needs it.")
    note = fields.Text()

    _code_uniq = models.Constraint("unique(code)", "The format code must be unique.")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def render(self, batch):
        """Return ``(bytes, filename)`` for the batch's transfer file."""
        self.ensure_one()
        self._validate_batch(batch)
        renderer = getattr(self, "_render_%s" % self.code, self._render_generic)
        content = renderer(batch)
        if isinstance(content, str):
            content = content.encode(self.encoding or "utf-8")
        filename = "%s_%s.%s" % (
            self.code,
            (batch.name or "batch").replace("/", "-"),
            self.file_extension or "csv",
        )
        return content, filename

    def _validate_batch(self, batch):
        missing = batch.payment_ids.filtered(lambda p: not p.partner_bank_id.acc_number)
        if missing:
            raise UserError(
                _(
                    "These payments have no recipient bank account number: %s. "
                    "Set the partner's bank account before exporting."
                )
                % ", ".join(missing.mapped("name"))
            )

    def _rows(self, batch):
        """Common row dicts every layout draws from."""
        rows = []
        for pay in batch.payment_ids.sorted("id"):
            bank = pay.partner_bank_id
            rows.append(
                {
                    "account": bank.acc_number.replace(" ", ""),
                    "holder": bank.acc_holder_name or pay.partner_id.name or "",
                    "partner": pay.partner_id.name or "",
                    "amount": pay.amount,
                    "date": (pay.date or batch.date).strftime(self.date_format or "%d/%m/%Y"),
                    "memo": pay.memo or pay.name or "",
                    "bic": bank.bank_id.bic or "",
                    "bank_name": bank.bank_id.name or "",
                }
            )
        return rows

    def _write_delimited(self, header, data_rows):
        buf = io.StringIO()
        delim = self.delimiter or ","
        if self.include_header and header:
            buf.write(delim.join(header) + "\r\n")
        for row in data_rows:
            buf.write(delim.join(str(c) for c in row) + "\r\n")
        return buf.getvalue()

    # -- generic fallback ------------------------------------------------
    def _render_generic(self, batch):
        rows = self._rows(batch)
        return self._write_delimited(
            ["Account Number", "Account Holder", "Amount", "Date", "Memo", "Bank BIC"],
            [[r["account"], r["holder"], "%.2f" % r["amount"], r["date"], r["memo"], r["bic"]] for r in rows],
        )

    # -- per-bank baselines (refine against real portal samples) --------
    def _render_bca_mcm(self, batch):
        # BCA MCM bulk-transfer CSV baseline: no header, amounts without
        # decimals (IDR), transaction type 0 = to-BCA / LLG left to the portal.
        rows = self._rows(batch)
        return self._write_delimited(
            None,
            [
                [
                    self.corporate_id or "",
                    self.debit_account or "",
                    r["account"],
                    "%.0f" % r["amount"],
                    r["holder"],
                    r["memo"],
                    r["date"],
                ]
                for r in rows
            ],
        )

    def _render_mandiri_mcm(self, batch):
        # Mandiri MCM (Cash Management) bulk payment baseline.
        rows = self._rows(batch)
        return self._write_delimited(
            ["No", "Beneficiary Account", "Beneficiary Name", "Amount", "Remark", "Value Date"],
            [
                [i + 1, r["account"], r["holder"], "%.2f" % r["amount"], r["memo"], r["date"]]
                for i, r in enumerate(rows)
            ],
        )

    def _render_bni(self, batch):
        rows = self._rows(batch)
        return self._write_delimited(
            ["Account No", "Name", "Amount", "Description", "Date"],
            [[r["account"], r["holder"], "%.2f" % r["amount"], r["memo"], r["date"]] for r in rows],
        )

    def _render_bri(self, batch):
        rows = self._rows(batch)
        return self._write_delimited(
            ["Account Number", "Recipient Name", "Amount", "Remark"],
            [[r["account"], r["holder"], "%.2f" % r["amount"], r["memo"]] for r in rows],
        )
