# -*- coding: utf-8 -*-
"""Coretax tenant configuration.

A stored singleton-style model (active record pattern). One record is
seeded by `data/coretax_data.xml`; additional records may exist but only
one should be marked `active=True` at a time. We use a stored model
rather than `res.config.settings` so that the sertel binary, NPWP, KPP
code, and adapter selection survive across settings rewrites and can be
referenced via xmlid from wizards.
"""

from __future__ import annotations

import base64
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# NPWP 16-digit (post-2024 NIK-based) or legacy 15-digit (xx.xxx.xxx.x-xxx.xxx)
_NPWP_DIGITS_RE = re.compile(r"^\d{15,16}$")


class CoretaxConfig(models.Model):
    _name = "custom.coretax.config"
    _description = "Custom Coretax — Tenant Configuration"
    _rec_name = "name"

    name = fields.Char(string="Display Name", required=True, default="Coretax Configuration")
    active = fields.Boolean(default=True, index=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
        index=True,
        help="Company that owns this Coretax configuration. Each company has its own "
        "NPWP, sertel, and adapter credentials.",
    )

    # ----- Taxpayer identity -----
    npwp = fields.Char(
        string="NPWP", size=16, required=True, help="NPWP perusahaan, digits only (15 legacy or 16 NIK-based)."
    )
    taxpayer_name = fields.Char(string="Nama Wajib Pajak", required=True)
    taxpayer_address = fields.Text(string="Alamat")
    kpp_code = fields.Char(string="KPP Code", size=3, help="3-digit Kantor Pelayanan Pajak code.")

    # ----- Sertifikat Elektronik (encrypted at rest) -----
    sertel_filename = fields.Char(string="Sertel Filename")
    sertel_data = fields.Binary(
        string="Sertel (.p12)",
        attachment=False,
        help="Stored AS NULL on this record; ciphertext lives "
        "in ir.config_parameter via custom.ir.config. Field is "
        "exposed only as an upload sink in the sertel wizard.",
    )
    sertel_uploaded = fields.Boolean(string="Sertel Uploaded", compute="_compute_sertel_uploaded")
    sertel_expiry = fields.Date(string="Sertel Expiry")

    # ----- Adapter selection -----
    adapter_type = fields.Selection(
        selection=[
            ("manual", "Manual (XML upload via DJP portal)"),
            ("h2h_aspp", "Host-to-Host via ASPP (future)"),
        ],
        string="Adapter",
        default="manual",
        required=True,
    )
    aspp_endpoint = fields.Char(string="ASPP Endpoint URL")
    aspp_credential_key = fields.Char(
        string="ASPP Credential Key",
        help="ir.config_parameter key under which the ASPP credential is stored "
        "encrypted via custom.ir.config. The credential plaintext is never "
        "stored on this record.",
    )

    _npwp_unique = models.Constraint(
        "unique(npwp)",
        "NPWP must be unique across Coretax configurations.",
    )

    @api.constrains("npwp")
    def _check_npwp(self):
        for rec in self:
            if rec.npwp and not _NPWP_DIGITS_RE.match(rec.npwp):
                raise ValidationError(
                    _("NPWP must be 15 or 16 digits (digits only, no dots/dashes). Got: %s") % rec.npwp
                )

    @api.constrains("kpp_code")
    def _check_kpp(self):
        for rec in self:
            if rec.kpp_code and not re.match(r"^\d{3}$", rec.kpp_code):
                raise ValidationError(_("KPP code must be exactly 3 digits."))

    def _compute_sertel_uploaded(self):
        IrConfig = self.env["custom.ir.config"]
        for rec in self:
            key = f"coretax.sertel.{rec.id}"
            rec.sertel_uploaded = bool(IrConfig.get_encrypted(key))

    # ----- Helpers -----
    @api.model
    def _get_active(self) -> "CoretaxConfig":
        """Return the active config record, raising if not configured."""
        rec = self.search([("active", "=", True)], limit=1)
        if not rec:
            raise ValidationError(_("No active custom.coretax.config record. Configure Coretax first."))
        return rec

    def get_sertel_p12(self) -> bytes | None:
        """Return raw .p12 bytes for the active config, or None if absent."""
        self.ensure_one()
        b64 = self.env["custom.ir.config"].get_encrypted(f"coretax.sertel.{self.id}")
        if not b64:
            return None
        try:
            return base64.b64decode(b64)
        except (ValueError, TypeError):
            _logger.error("coretax: malformed sertel ciphertext for config %s", self.id)
            return None
