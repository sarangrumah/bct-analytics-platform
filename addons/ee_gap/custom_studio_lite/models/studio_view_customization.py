# -*- coding: utf-8 -*-
"""Declarative view editor — generates ir.ui.view inheritance records.

Users declare an ordered list of operations (add field, hide field,
move field, set attribute) against a target view; ``action_apply``
emits a single ``<data>``-wrapped XPath snippet and upserts an
``ir.ui.view`` with ``inherit_id`` set. The materialised inheritance is
remembered so re-apply updates instead of duplicating.

Phase 1 deliberately ships only this form-driven model. The OWL
drag-drop editor planned for Phase 2 will write the same operation
list to this storage.
"""

from __future__ import annotations

import logging
from xml.sax.saxutils import escape, quoteattr

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

OP_TYPES = [
    ("add_field", "Add Field"),
    ("hide_field", "Hide Field"),
    ("move_field", "Move Field"),
    ("set_attr", "Set Attribute"),
]

POSITIONS = [
    ("before", "Before anchor"),
    ("after", "After anchor"),
    ("inside", "Inside anchor"),
    ("replace", "Replace anchor"),
]

# Attribute whitelist for set_attr ops. Anything outside this list is
# rejected to keep the surface area auditable.
ATTR_WHITELIST = {"invisible", "readonly", "required", "widget", "string", "groups", "help"}


class StudioViewCustomization(models.Model):
    _name = "studio.view.customization"
    _description = "Studio View Customization"
    _inherit = ["pdp.audited.mixin"]
    _order = "target_model_name, name"

    name = fields.Char(required=True, translate=True)
    target_view_id = fields.Many2one(
        "ir.ui.view",
        string="Target View",
        required=True,
        ondelete="cascade",
        domain="[('type', 'in', ('form', 'list', 'kanban', 'search'))]",
    )
    target_model_id = fields.Many2one(
        "ir.model",
        string="Target Model",
        compute="_compute_target_model",
        store=True,
        readonly=True,
    )
    target_model_name = fields.Char(related="target_model_id.model", store=True, readonly=True)
    operation_ids = fields.One2many("studio.view.operation", "customization_id", string="Operations", copy=True)
    state = fields.Selection(
        [("draft", "Draft"), ("applied", "Applied"), ("error", "Error")],
        default="draft",
        required=True,
    )
    arch_inherit = fields.Text(string="Generated Arch", readonly=True)
    inherit_view_id = fields.Many2one(
        "ir.ui.view",
        string="Materialised Inheritance",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    last_error = fields.Text(readonly=True)
    active = fields.Boolean(default=True)

    def _pdp_audit_classification(self):
        return "internal"

    @api.depends("target_view_id")
    def _compute_target_model(self):
        Model = self.env["ir.model"].sudo()
        for rec in self:
            if rec.target_view_id and rec.target_view_id.model:
                rec.target_model_id = Model.search([("model", "=", rec.target_view_id.model)], limit=1)
            else:
                rec.target_model_id = False

    # ---------- Apply ----------

    def action_apply(self):
        View = self.env["ir.ui.view"].sudo()
        for rec in self:
            try:
                if not rec.operation_ids:
                    # No operations left — deactivate any prior inheritance
                    # so the view reverts to its base arch. This is the
                    # path the overlay "Ops" tab takes when the user
                    # deletes every op individually.
                    if rec.inherit_view_id:
                        rec.inherit_view_id.write({"active": False, "arch": "<data/>"})
                    rec.write({"state": "draft", "last_error": False, "arch_inherit": False})
                    continue
                arch = rec._build_arch()
                rec.arch_inherit = arch
                vals = {
                    "name": f"studio.custom.{rec.target_view_id.id}.{rec.id}",
                    "model": rec.target_view_id.model,
                    "inherit_id": rec.target_view_id.id,
                    "mode": "extension",
                    "type": rec.target_view_id.type,
                    "arch": arch,
                    "active": rec.active,
                    "priority": 80,
                }
                if rec.inherit_view_id:
                    rec.inherit_view_id.write(vals)
                    inherit_view = rec.inherit_view_id
                else:
                    inherit_view = View.create(vals)
                    rec.inherit_view_id = inherit_view.id
                # Validate the combined arch — raises if the XPath is broken.
                # (Odoo 19 renamed ``read_combined`` → ``get_combined_arch``.)
                rec.target_view_id.with_context(check_view_ids=inherit_view.ids).get_combined_arch()
                rec.write({"state": "applied", "last_error": False})
                rec._pdp_audit_write(
                    "studio_view_applied",
                    rec.id,
                    {"target": rec.target_view_id.xml_id or rec.target_view_id.name},
                )
            except Exception as e:
                _logger.exception("studio.view.customization %s apply failed", rec.id)
                rec.write({"state": "error", "last_error": str(e)})
                # Best-effort rollback: deactivate the inheritance so the
                # main view still renders.
                if rec.inherit_view_id:
                    try:
                        rec.inherit_view_id.write({"active": False})
                    except Exception:
                        pass
                rec._pdp_audit_write("studio_view_apply_failed", rec.id, {"error": str(e)})

    def action_revert(self):
        for rec in self:
            if rec.inherit_view_id:
                rec.inherit_view_id.write({"active": False})
            rec.write({"state": "draft"})

    def _build_arch(self) -> str:
        self.ensure_one()
        # ir.ui.view.arch rejects strings carrying an <?xml?> declaration —
        # it expects fragment XML and parses with lxml in unicode mode.
        parts = ["<data>"]
        for op in self.operation_ids.sorted("sequence"):
            parts.append(op._render())
        parts.append("</data>")
        return "\n".join(parts)


class StudioViewOperation(models.Model):
    _name = "studio.view.operation"
    _description = "Studio View Operation"
    _order = "customization_id, sequence, id"

    customization_id = fields.Many2one("studio.view.customization", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    op_type = fields.Selection(OP_TYPES, required=True, default="add_field")
    field_name = fields.Char(help="Name of the field this op targets (the field being added/hidden/moved/edited).")
    anchor_field = fields.Char(help="Name of the existing field used as the XPath anchor.")
    position = fields.Selection(POSITIONS, default="after")
    attr_name = fields.Char(help="Attribute name for set_attr (e.g. invisible, required, widget).")
    attr_value = fields.Char(help="Attribute value for set_attr (e.g. 1, state == 'draft').")

    @api.constrains("op_type", "field_name", "anchor_field", "attr_name", "attr_value")
    def _check_op(self):
        for op in self:
            if op.op_type in {"add_field", "hide_field", "move_field"} and not op.field_name:
                raise ValidationError(_("Field name is required for %s.") % op.op_type)
            if op.op_type in {"add_field", "move_field"} and not op.anchor_field:
                raise ValidationError(_("Anchor field is required for %s.") % op.op_type)
            if op.op_type == "set_attr":
                if not op.field_name:
                    raise ValidationError(_("set_attr needs a field_name (the field to modify)."))
                if not op.attr_name:
                    raise ValidationError(_("set_attr needs an attribute name."))
                if op.attr_name not in ATTR_WHITELIST:
                    raise ValidationError(
                        _("Attribute '%s' is not in the allowed list: %s")
                        % (op.attr_name, ", ".join(sorted(ATTR_WHITELIST)))
                    )

    def _render(self) -> str:
        """Render this operation as an <xpath> fragment.

        Note: the ``expr`` attribute itself uses double quotes (XML
        attribute delimiter), so the field name embedded inside the
        XPath predicate uses *single* quotes — nesting ``quoteattr`` of a
        double-quoted string inside another double-quoted attribute
        produces invalid XML at parse time.
        """
        self.ensure_one()
        # Field/attr names are restricted by Odoo's field naming rules
        # (lowercase + underscore + digits); they cannot contain quotes
        # and are safe to interpolate inside an XPath predicate.
        if self.op_type == "add_field":
            anchor = self.anchor_field or ""
            field = escape(self.field_name or "")
            pos = self.position or "after"
            return (
                f'  <xpath expr="//field[@name=\'{anchor}\']" position={quoteattr(pos)}><field name="{field}"/></xpath>'
            )
        if self.op_type == "hide_field":
            field = self.field_name or ""
            # Match any named element — ``<field>`` and ``<widget>`` both
            # honour the ``invisible`` attribute, so this lets Studio
            # hide things like ``<widget name="web_ribbon" .../>`` or
            # ``<button name="action_x"/>`` too.
            return (
                f'  <xpath expr="//*[@name=\'{field}\']" position="attributes">'
                f'<attribute name="invisible">1</attribute></xpath>'
            )
        if self.op_type == "move_field":
            anchor = self.anchor_field or ""
            field = escape(self.field_name or "")
            pos = self.position or "after"
            return (
                f"  <xpath expr=\"//field[@name='{anchor}']\" position={quoteattr(pos)}>"
                f'<field name="{field}" position="move"/></xpath>'
            )
        if self.op_type == "set_attr":
            field = self.field_name or ""
            attr = escape(self.attr_name or "")
            value = escape(self.attr_value or "")
            return (
                f'  <xpath expr="//field[@name=\'{field}\']" position="attributes">'
                f'<attribute name="{attr}">{value}</attribute></xpath>'
            )
        return ""
