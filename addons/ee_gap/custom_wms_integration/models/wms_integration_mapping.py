# -*- coding: utf-8 -*-
"""Host-code <-> Odoo-record translation table.

The host almost never uses Odoo's identifiers. SAP sends a MATNR, the WMS sends
its own bin code, the 3PL sends its own vendor number. This model holds the
explicit overrides; everything that already lines up resolves through the
natural fallbacks (``product.default_code``, ``stock.location.barcode`` /
``complete_name``, ``res.partner.ref``) without needing a row.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

INTERNAL_MODELS = [
    ("product.product", "Product"),
    ("stock.location", "Location"),
    ("res.partner", "Partner"),
]

DIRECTIONS = [
    ("both", "Both"),
    ("inbound", "Inbound (host -> Odoo)"),
    ("outbound", "Outbound (Odoo -> host)"),
]


class WmsIntegrationMapping(models.Model):
    _name = "wms.integration.mapping"
    _description = "WMS Integration Code Mapping"
    _inherit = ["pdp.audited.mixin"]
    _order = "internal_model, external_code"
    _rec_name = "external_code"

    external_code = fields.Char(required=True, index=True, help="The code as the host system knows it.")
    internal_model = fields.Selection(INTERNAL_MODELS, required=True, index=True)
    internal_res_id = fields.Integer(required=True, index=True, string="Internal Record ID")
    internal_display = fields.Char(compute="_compute_internal_display", string="Internal Record")
    direction = fields.Selection(DIRECTIONS, default="both", required=True, index=True)
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        index=True,
        help="Leave empty to make the mapping apply to every company.",
    )
    active = fields.Boolean(default=True)
    notes = fields.Text()

    # Odoo 19 silently ignores _sql_constraints; models.Constraint is the only
    # form that actually reaches PostgreSQL.
    _external_code_uniq = models.Constraint(
        "unique(external_code, internal_model, direction, company_id)",
        "A host code can only map once per model/direction/company.",
    )

    @api.depends("internal_model", "internal_res_id")
    def _compute_internal_display(self):
        for rec in self:
            rec.internal_display = ""
            if not rec.internal_model or not rec.internal_res_id:
                continue
            record = self.env[rec.internal_model].browse(rec.internal_res_id).exists()
            rec.internal_display = record.display_name if record else _("<deleted #%s>") % rec.internal_res_id

    @api.constrains("internal_model", "internal_res_id")
    def _check_internal_record(self):
        for rec in self:
            if rec.internal_model and rec.internal_res_id:
                if not self.env[rec.internal_model].browse(rec.internal_res_id).exists():
                    raise ValidationError(
                        _("No %(model)s with id %(rid)s.", model=rec.internal_model, rid=rec.internal_res_id)
                    )

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    @api.model
    def _resolve(self, external_code, model, company=None, direction="inbound"):
        """Resolve a host code to an Odoo recordset of ``model``.

        Order of precedence:
          1. an explicit ``wms.integration.mapping`` row (company-specific first,
             then the company-agnostic one),
          2. the model's natural key fallback,
          3. an empty recordset.

        Never raises — callers decide what an unresolvable code means.
        """
        Model = self.env[model]
        code = (external_code or "").strip()
        if not code:
            return Model.browse()

        company = company or self.env.company
        domain = [
            ("external_code", "=", code),
            ("internal_model", "=", model),
            ("direction", "in", (direction, "both")),
            ("company_id", "in", (company.id, False)),
        ]
        # company_id desc puts the company-specific row ahead of the global one.
        mapping = self.sudo().search(domain, order="company_id desc, id asc", limit=1)
        if mapping:
            record = Model.browse(mapping.internal_res_id).exists()
            if record:
                return record
            _logger.warning(
                "wms.integration.mapping %s points at a deleted %s#%s",
                mapping.id,
                model,
                mapping.internal_res_id,
            )

        return self._resolve_fallback(code, model, company)

    @api.model
    def _resolve_fallback(self, code, model, company):
        """Natural-key fallback used when no explicit mapping row exists."""
        Model = self.env[model].sudo()
        company_domain = ["|", ("company_id", "=", False), ("company_id", "=", company.id)]

        if model == "product.product":
            for field_name in ("default_code", "barcode"):
                if field_name not in Model._fields:
                    continue
                found = Model.search([(field_name, "=", code)] + company_domain, limit=1)
                if found:
                    return found
            return Model.browse()

        if model == "stock.location":
            for field_name in ("barcode", "complete_name", "name"):
                if field_name not in Model._fields:
                    continue
                found = Model.search([(field_name, "=", code)] + company_domain, limit=1)
                if found:
                    return found
            return Model.browse()

        if model == "res.partner":
            for field_name in ("ref", "vat", "name"):
                found = Model.search([(field_name, "=", code)], limit=1)
                if found:
                    return found
            return Model.browse()

        return Model.browse()

    @api.model
    def _external_code_for(self, record, direction="outbound"):
        """Reverse lookup: the code the host expects for an Odoo record.

        Falls back to the natural key so an un-mapped record still pushes
        something meaningful rather than an empty string.
        """
        if not record:
            return ""
        mapping = self.sudo().search(
            [
                ("internal_model", "=", record._name),
                ("internal_res_id", "=", record.id),
                ("direction", "in", (direction, "both")),
            ],
            order="company_id desc, id asc",
            limit=1,
        )
        if mapping:
            return mapping.external_code
        for field_name in ("default_code", "barcode", "ref", "complete_name", "name"):
            if field_name in record._fields and record[field_name]:
                return record[field_name]
        return record.display_name or ""
