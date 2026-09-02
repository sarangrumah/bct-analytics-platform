# -*- coding: utf-8 -*-
"""Wizard for rename/delete operations on a custom field with dependent views.

Listing the affected views and asking the user to confirm cascade is the
safe alternative to silent destruction. Cascading rewrites the dependent
views' arch_db (rename) or strips the field reference (delete).
"""

from __future__ import annotations

import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StudioFieldImpactWizard(models.TransientModel):
    _name = "studio.field.impact.wizard"
    _description = "Studio Field Impact Wizard"

    custom_field_id = fields.Many2one("studio.custom.field", required=True, ondelete="cascade")
    operation = fields.Selection(
        [("rename", "Rename"), ("delete", "Delete")],
        required=True,
        default="delete",
    )
    new_technical_name = fields.Char(
        help="Required for rename. Must match the x_studio_ pattern.",
    )
    view_ids = fields.Many2many(
        "ir.ui.view",
        string="Affected Views",
        readonly=True,
    )
    cascade = fields.Boolean(
        default=True,
        help="When ticked, rewrite the dependent views automatically. When unticked, the operation is blocked.",
    )
    summary = fields.Text(compute="_compute_summary")

    @api.depends("custom_field_id", "operation", "view_ids")
    def _compute_summary(self):
        for rec in self:
            n = len(rec.view_ids)
            field = rec.custom_field_id.technical_name or "?"
            if rec.operation == "rename":
                rec.summary = _(
                    "Renaming field '%s' will update %d view(s). All occurrences in "
                    "their arch will be replaced with the new technical name."
                ) % (field, n)
            else:
                rec.summary = _(
                    "Deleting field '%s' will strip its references from %d view(s) "
                    "and drop the underlying database column."
                ) % (field, n)

    def action_confirm(self):
        self.ensure_one()
        if not self.cascade and self.view_ids:
            raise UserError(_("Cascade is disabled and there are dependent views — operation cancelled."))
        if self.operation == "rename":
            return self._do_rename()
        return self._do_delete()

    def _do_rename(self):
        field = self.custom_field_id
        new_name = (self.new_technical_name or "").strip()
        if not new_name:
            raise UserError(_("Provide a new technical name."))
        old_name = field.technical_name
        # Renaming a field that is referenced by views is delicate:
        # ``ir.model.fields._prepare_update`` searches for views whose
        # arch references the old name and revalidates them, and the
        # substring ``LIKE`` match also catches views already rewritten to
        # the new name. To break the chicken-and-egg cleanly:
        #   1. Snapshot + deactivate the dependent views (no longer in
        #      the combined arch, so _prepare_update's revalidation is a
        #      no-op).
        #   2. Rename the ir.model.fields row + descriptor.
        #   3. SQL-rewrite the deactivated views' arch_db.
        #   4. Reactivate them — _check_xml now finds the new field name.
        # The surrounding transaction wraps everything; any failure rolls
        # the whole rename back.
        dependents = field.dependent_view_ids
        was_active = {v.id: v.active for v in dependents}
        if dependents:
            dependents.with_context(skip_check_xml=True).write({"active": False})
        if field.ir_model_fields_id:
            field.ir_model_fields_id.sudo().write({"name": new_name})
        field.write({"technical_name": new_name})
        if dependents:
            field._sql_rename_in_views(old_name, new_name, dependents)
            # Reactivate views one by one to fail fast on a broken arch.
            for view in dependents:
                if was_active.get(view.id, True):
                    view.write({"active": True})
        return {"type": "ir.actions.act_window_close"}

    def _do_delete(self):
        field = self.custom_field_id
        # Strip references from each dependent view.
        if self.view_ids:
            field_name = field.technical_name
            # Remove <field name="..."/> standalone nodes that reference the field.
            # XPath-like regex: matches the opening tag and any whitespace/attrs.
            pattern = re.compile(
                r'<field\s+[^>]*name="' + re.escape(field_name) + r'"[^/>]*/?\s*>(\s*</field>)?',
                re.DOTALL,
            )
            for view in self.view_ids:
                original = view.arch_db or ""
                stripped, _n = pattern.subn("", original)
                if stripped != original:
                    view.write({"arch_db": stripped})
        field.with_context(force_cascade=True).unlink()
        return {"type": "ir.actions.act_window_close"}
