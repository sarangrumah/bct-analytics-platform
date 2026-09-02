# -*- coding: utf-8 -*-
"""Indonesian bank master data: the BI clearing/RTGS participant code.

Odoo core ``res.bank`` carries the SWIFT/BIC in ``bic``, but Indonesian banking
also identifies a bank by its *sandi bank* — the 7-digit Bank Indonesia
clearing/RTGS participant code. BIC is not a usable key here: every BI branch
office shares ``INDOIDJA``, so the BI code is what uniquely identifies a row.
"""

from __future__ import annotations

from odoo import fields, models


class ResBank(models.Model):
    _inherit = "res.bank"

    l10n_id_bi_code = fields.Char(
        string="Kode BI",
        index=True,
        help="Sandi bank / kode peserta kliring-RTGS Bank Indonesia (7 digit).",
    )

    # NULL is allowed many times over in Postgres, so banks without a BI code
    # (e.g. the stock "Reserve" placeholder) do not collide.
    _l10n_id_bi_code_uniq = models.Constraint(
        "unique(l10n_id_bi_code)",
        "Kode BI sudah dipakai bank lain.",
    )
