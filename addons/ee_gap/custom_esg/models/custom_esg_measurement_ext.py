# -*- coding: utf-8 -*-
"""Extend measurement with auditor-evidence fields.

These complement the existing draft → validated → audited workflow by
attaching the supporting document and an auditor's hash/signature string.
"""

from __future__ import annotations

from odoo import fields, models


class CustomEsgMeasurement(models.Model):
    _inherit = "custom.esg.measurement"

    x_audit_evidence = fields.Binary(
        string="Audit Evidence (file)",
        attachment=True,
        help="Supporting document attached at audit time (PDF, image, etc.).",
    )
    x_audit_evidence_filename = fields.Char(
        string="Audit Evidence Filename",
    )
    x_auditor_signature = fields.Char(
        string="Auditor Signature / Hash",
        tracking=True,
        help=(
            "Auditor's signature payload — typically a SHA-256 hex digest of the evidence file plus auditor identity."
        ),
    )
