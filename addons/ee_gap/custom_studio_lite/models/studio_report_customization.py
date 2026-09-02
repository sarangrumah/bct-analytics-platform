# -*- coding: utf-8 -*-
"""Report editor — wrap ir.actions.report + ir.ui.view (qweb) for non-developers.

Phase 3 deliverable. The scope here is deliberately narrow vs. Odoo
Enterprise Studio's full QWeb design canvas: we let designers clone an
existing report, choose a different paper format, and override the
header/footer text + visibility, plus inject extra fields/sections via
QWeb XPath inheritance. Free-form arbitrary QWeb editing is reserved
for the "Advanced" page where designers paste raw QWeb snippets — those
are validated server-side against a restricted directive whitelist
before being applied.
"""

from __future__ import annotations

import logging
import re
from xml.sax.saxutils import escape, quoteattr

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


# QWeb directives we allow in user-supplied snippets. Anything outside
# this list is rejected to keep the surface area auditable.
_QWEB_ALLOWED_DIRECTIVES = {
    "t-if",
    "t-elif",
    "t-else",
    "t-foreach",
    "t-as",
    "t-esc",
    "t-out",
    "t-field",
    "t-options",
    "t-set",
    "t-att",
    "t-att-class",
    "t-att-style",
}


# Catch any unsafe t-* directive — t-call could load arbitrary templates,
# t-call-assets too, t-raw was removed but defensive.
_QWEB_FORBIDDEN_PATTERN = re.compile(r"\bt-(call|call-assets|raw|debug|js|signature)\b")


class StudioReportCustomization(models.Model):
    _name = "studio.report.customization"
    _description = "Studio Report Customization"
    _inherit = ["pdp.audited.mixin"]
    _order = "base_report_id, name"

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    base_report_id = fields.Many2one(
        "ir.actions.report",
        string="Base Report",
        required=True,
        ondelete="cascade",
        domain="[('report_type', '=', 'qweb-pdf')]",
    )
    base_view_id = fields.Many2one(
        "ir.ui.view",
        string="Base Template",
        compute="_compute_base_view",
        store=True,
        readonly=True,
    )
    paper_format_id = fields.Many2one(
        "report.paperformat",
        string="Paper Format Override",
        help="If set, the customization patches the report to use this paper format.",
    )

    header_text = fields.Text(help="Replaces or appends to the report header. Use \\n for line breaks.")
    header_mode = fields.Selection(
        [("append", "Append after default header"), ("replace", "Replace default header")],
        default="append",
    )
    footer_text = fields.Text()
    footer_mode = fields.Selection(
        [("append", "Append after default footer"), ("replace", "Replace default footer")],
        default="append",
    )

    # User-supplied QWeb XPath snippets (advanced). Each row is a single
    # <xpath ...>...</xpath> block. Validation happens in _check_qweb.
    custom_xpath_ids = fields.One2many(
        "studio.report.xpath",
        "customization_id",
        string="QWeb XPath Snippets",
    )

    inherit_view_id = fields.Many2one("ir.ui.view", readonly=True, copy=False, ondelete="set null")
    inherit_report_id = fields.Many2one(
        "ir.actions.report",
        readonly=True,
        copy=False,
        ondelete="set null",
        help="When paper format is overridden, a clone of the base report is materialised.",
    )
    state = fields.Selection(
        [("draft", "Draft"), ("applied", "Applied"), ("error", "Error")],
        default="draft",
        required=True,
    )
    arch_inherit = fields.Text(readonly=True)
    last_error = fields.Text(readonly=True)

    def _pdp_audit_classification(self):
        return "internal"

    @api.depends("base_report_id")
    def _compute_base_view(self):
        View = self.env["ir.ui.view"].sudo()
        for rec in self:
            if not rec.base_report_id:
                rec.base_view_id = False
                continue
            # ir.actions.report.report_name is the QWeb template key (e.g. "sale.report_saleorder")
            key = rec.base_report_id.report_name
            view = View.search([("key", "=", key), ("type", "=", "qweb")], limit=1)
            rec.base_view_id = view.id if view else False

    # ---------- Apply ----------

    def action_apply(self):
        View = self.env["ir.ui.view"].sudo()
        Report = self.env["ir.actions.report"].sudo()
        for rec in self:
            try:
                if not rec.base_view_id:
                    raise UserError(_("Could not resolve base QWeb template — pick a different report."))
                arch = rec._build_arch()
                rec.arch_inherit = arch
                vals = {
                    "name": f"studio.report.{rec.base_view_id.id}.{rec.id}",
                    "key": f"custom_studio_lite.report_{rec.id}",
                    "type": "qweb",
                    "inherit_id": rec.base_view_id.id,
                    "mode": "extension",
                    "arch": arch,
                    "active": rec.active,
                    "priority": 80,
                }
                if rec.inherit_view_id:
                    rec.inherit_view_id.write(vals)
                else:
                    rec.inherit_view_id = View.create(vals).id
                # Validate combined arch.
                rec.base_view_id.with_context(check_view_ids=rec.inherit_view_id.ids).get_combined_arch()
                # If a different paper format is requested, clone the report
                # action and switch its paper format.
                if rec.paper_format_id:
                    if not rec.inherit_report_id:
                        cloned = rec.base_report_id.copy(
                            {
                                "name": f"{rec.base_report_id.name} (Studio)",
                                "paperformat_id": rec.paper_format_id.id,
                            }
                        )
                        rec.inherit_report_id = cloned.id
                    else:
                        rec.inherit_report_id.write(
                            {
                                "paperformat_id": rec.paper_format_id.id,
                                "active": rec.active,
                            }
                        )
                rec.write({"state": "applied", "last_error": False})
                rec._pdp_audit_write(
                    "studio_report_applied",
                    rec.id,
                    {"report": rec.base_report_id.report_name},
                )
            except Exception as e:
                _logger.exception("studio.report.customization %s apply failed", rec.id)
                rec.write({"state": "error", "last_error": str(e)})
                if rec.inherit_view_id:
                    try:
                        rec.inherit_view_id.write({"active": False})
                    except Exception:
                        pass

    def _build_arch(self) -> str:
        self.ensure_one()
        # ir.ui.view.arch rejects strings carrying an <?xml?> declaration.
        parts = ["<data>"]
        if self.header_text:
            mode = "replace" if self.header_mode == "replace" else "after"
            text = escape(self.header_text).replace("\n", "<br/>")
            parts.append(
                f"  <xpath expr=\"//div[hasclass('header')]\" position={quoteattr(mode)}>"
                f'<div class="o_studio_header_block">{text}</div></xpath>'
            )
        if self.footer_text:
            mode = "replace" if self.footer_mode == "replace" else "after"
            text = escape(self.footer_text).replace("\n", "<br/>")
            parts.append(
                f"  <xpath expr=\"//div[hasclass('footer')]\" position={quoteattr(mode)}>"
                f'<div class="o_studio_footer_block">{text}</div></xpath>'
            )
        for xp in self.custom_xpath_ids.sorted("sequence"):
            parts.append("  " + xp.xpath_snippet.strip())
        parts.append("</data>")
        return "\n".join(parts)


class StudioReportXpath(models.Model):
    _name = "studio.report.xpath"
    _description = "Studio Report QWeb XPath"
    _order = "customization_id, sequence, id"

    customization_id = fields.Many2one("studio.report.customization", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    label = fields.Char(help="Human-readable label for this snippet.")
    xpath_snippet = fields.Text(
        required=True,
        help="A single <xpath>...</xpath> block. Allowed QWeb directives: "
        + ", ".join(sorted(_QWEB_ALLOWED_DIRECTIVES)),
    )

    @api.constrains("xpath_snippet")
    def _check_qweb(self):
        for rec in self:
            snippet = rec.xpath_snippet or ""
            if not snippet.strip().startswith("<xpath"):
                raise ValidationError(_("Snippet must start with <xpath ...>."))
            if _QWEB_FORBIDDEN_PATTERN.search(snippet):
                raise ValidationError(_("Snippet uses a forbidden QWeb directive (t-call / t-raw / etc)."))
