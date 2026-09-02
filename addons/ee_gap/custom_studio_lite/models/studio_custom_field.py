# -*- coding: utf-8 -*-
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


SUPPORTED_TYPES = [
    ("char", "Text (single line)"),
    ("text", "Text (multi-line)"),
    ("integer", "Integer"),
    ("float", "Float"),
    ("boolean", "Boolean"),
    ("date", "Date"),
    ("datetime", "Datetime"),
    ("selection", "Selection"),
    ("many2one", "Many2one"),
    ("many2many", "Many2many"),
    ("one2many", "One2many"),
]

RELATIONAL_TYPES = {"many2one", "many2many", "one2many"}

FIELD_NAME_RE = re.compile(r"^x_studio_[a-z0-9_]{1,60}$")


class StudioCustomField(models.Model):
    _name = "studio.custom.field"
    _description = "Studio Custom Field (declarative)"
    _inherit = ["pdp.audited.mixin"]
    _order = "model_id, technical_name"

    name = fields.Char(string="Label", required=True, translate=True)
    technical_name = fields.Char(
        required=True,
        help="Must start with x_studio_ — enforced at create time.",
    )
    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade")
    model_name = fields.Char(related="model_id.model", store=True, readonly=True)
    field_type = fields.Selection(SUPPORTED_TYPES, required=True, default="char")
    selection_values = fields.Text(
        help="One key|label pair per line. Only used when field_type=selection.",
    )
    help_text = fields.Char()
    required = fields.Boolean()
    readonly = fields.Boolean()

    # Relational targets — only used when field_type is m2o/m2m/o2m.
    relation_model_id = fields.Many2one(
        "ir.model",
        string="Related Model",
        help="Target model for relational fields (m2o, m2m, o2m).",
    )
    relation_field_id = fields.Many2one(
        "ir.model.fields",
        string="Inverse Field",
        domain="[('model_id', '=', relation_model_id), ('ttype', '=', 'many2one')]",
        help="One2many inverse: the Many2one on the related model that points back here.",
    )
    relation_table = fields.Char(
        string="M2M Link Table",
        help="Many2many link table name. Auto-derived as x_<model>_<field>_rel if blank.",
    )

    # Materialised fields tracking.
    ir_model_fields_id = fields.Many2one("ir.model.fields", readonly=True, copy=False)
    state = fields.Selection(
        [("draft", "Draft"), ("applied", "Applied"), ("error", "Error")],
        default="draft",
        required=True,
    )
    last_error = fields.Text(readonly=True)

    # ---- Field-view impact tracking ----
    dependent_view_ids = fields.Many2many(
        "ir.ui.view",
        compute="_compute_dependent_views",
        string="Views Using This Field",
        help="Views whose arch_db references this field name. Recomputed on demand.",
    )
    dependent_view_count = fields.Integer(compute="_compute_dependent_views")

    _uniq_model_field = models.Constraint(
        "unique(model_id, technical_name)",
        "Field name already exists on this model.",
    )

    def _pdp_audit_classification(self):
        return "internal"

    @api.constrains("technical_name")
    def _check_technical_name(self):
        for rec in self:
            if not FIELD_NAME_RE.match(rec.technical_name or ""):
                raise ValidationError(_("Technical name must match %s") % FIELD_NAME_RE.pattern)

    @api.constrains("field_type", "relation_model_id", "relation_field_id")
    def _check_relational(self):
        for rec in self:
            if rec.field_type in RELATIONAL_TYPES and not rec.relation_model_id:
                raise ValidationError(_("Relational field %s requires a target model.") % rec.technical_name)
            if rec.field_type == "one2many" and not rec.relation_field_id:
                raise ValidationError(_("One2many %s requires an inverse Many2one field.") % rec.technical_name)

    # ---------- Impact analysis ----------

    def _compute_dependent_views(self):
        """Find views whose arch references this field.

        ``arch_db`` is a JSONB column in Odoo 19 (``{"en_US": "..."}``),
        so a plain ``ilike`` domain does not reach into the stored XML.
        We narrow the candidate set with the cheap ``model`` filter and
        then scan ``arch`` (the computed text) in Python. Matches either
        ``name="x"`` (direct field ref) or ``@name='x'`` (XPath predicate).
        """
        View = self.env["ir.ui.view"].sudo()
        for rec in self:
            if not rec.technical_name or not rec.model_name:
                rec.dependent_view_ids = False
                rec.dependent_view_count = 0
                continue
            candidates = View.search([("model", "=", rec.model_name)])
            needles = (
                f'name="{rec.technical_name}"',
                f"name='{rec.technical_name}'",
            )
            matching = candidates.filtered(lambda v: any(n in (v.arch or "") for n in needles))
            rec.dependent_view_ids = [(6, 0, matching.ids)]
            rec.dependent_view_count = len(matching)

    def action_refresh_impact(self):
        self.invalidate_recordset(["dependent_view_ids", "dependent_view_count"])
        return True

    def action_show_impact_wizard(self, op):
        """Open the impact wizard for either ``rename`` or ``delete``."""
        self.ensure_one()
        wizard = self.env["studio.field.impact.wizard"].create(
            {
                "custom_field_id": self.id,
                "operation": op,
                "view_ids": [(6, 0, self.dependent_view_ids.ids)],
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "studio.field.impact.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_delete_with_impact_check(self):
        """Button on the form — checks for dependents and routes to the wizard or unlinks."""
        self.ensure_one()
        if self.dependent_view_count:
            return self.action_show_impact_wizard("delete")
        return self.unlink()

    def unlink(self):
        Op = self.env["studio.view.operation"].sudo()
        Cust = self.env["studio.view.customization"].sudo()
        impacted_cust_ids = set()
        for rec in self:
            if rec.dependent_view_count and not self.env.context.get("force_cascade"):
                raise UserError(
                    _(
                        "Cannot delete field %s: %d view(s) still reference it. "
                        "Run the impact wizard to cascade-remove references first."
                    )
                    % (rec.technical_name, rec.dependent_view_count)
                )
            # Sweep any studio.view.operation rows referencing this
            # field by name (as the field being targeted OR as the
            # anchor for someone else's op) — leaving them in place
            # produces orphan inheritances that fail validation on the
            # next apply, which is what was breaking the
            # "delete-then-re-add same name" flow.
            orphan_ops = Op.search(
                [
                    "|",
                    ("field_name", "=", rec.technical_name),
                    ("anchor_field", "=", rec.technical_name),
                ]
            )
            impacted_cust_ids.update(orphan_ops.mapped("customization_id").ids)
            orphan_ops.unlink()
            if rec.ir_model_fields_id:
                # Drop the materialised ir.model.fields too — cascading there
                # is what actually removes the DB column.
                try:
                    rec.ir_model_fields_id.sudo().unlink()
                except Exception as e:
                    _logger.warning(
                        "studio.custom.field %s: failed to drop ir.model.fields: %s",
                        rec.id,
                        e,
                    )
        result = super().unlink()
        # Re-apply impacted customizations so their inherit_view loses
        # the dangling <field> reference too.
        if impacted_cust_ids:
            Cust.browse(list(impacted_cust_ids)).action_apply()
        return result

    def _sql_rename_in_views(self, old_name: str, new_name: str, views):
        """Rewrite ``name="old"`` / ``name='old'`` to the new name via SQL.

        Goes through the database directly to skip ir.ui.view._check_xml,
        which would otherwise reject the transition state (the new field
        does not exist on the model yet). The calling wizard renames the
        ir.model.fields row immediately after this returns, restoring
        consistency before the transaction commits.

        Each view's ``arch_db`` is a translated JSONB column whose keys
        are language codes (``{"en_US": "<form>...</form>"}``). We rewrite
        every language entry. A single transaction-scoped savepoint covers
        all views; any failure rolls everything back via the surrounding
        wizard transaction.
        """
        self.ensure_one()
        if not views or old_name == new_name or not old_name:
            return
        # Update every language string in each view's JSONB arch_db.
        # ``value #>> '{}'`` extracts the JSONB scalar string as unescaped
        # text (so we don't have to escape the inner quotes in the regex);
        # ``to_jsonb(...)`` re-encodes the result as a JSONB scalar.
        self.env.cr.execute(
            r"""
            UPDATE ir_ui_view AS v
            SET arch_db = (
                SELECT jsonb_object_agg(
                    key,
                    to_jsonb(
                        regexp_replace(
                            value #>> '{}',
                            %(pattern)s,
                            E'name=\\1' || %(new_name)s || E'\\1',
                            'g'
                        )
                    )
                )
                FROM jsonb_each(v.arch_db)
            )
            WHERE v.id = ANY(%(ids)s)
              AND v.arch_db IS NOT NULL
            """,
            {
                "pattern": r"""name=("|')""" + re.escape(old_name) + r"""\1""",
                "new_name": new_name,
                "ids": list(views.ids),
            },
        )
        # Bust caches so any subsequent read returns the rewritten arch.
        views.invalidate_recordset(["arch_db"])

    def _propagate_rename(self, old_name: str, new_name: str, views=None):
        """ORM-based rename (legacy path). Validates each view; rolls back on failure.

        The wizard now prefers :meth:`_sql_rename_in_views`; this method is
        kept for callers that need post-write validation, e.g. when the
        new name already exists on the model.
        """
        self.ensure_one()
        if old_name == new_name or not old_name:
            return
        View = self.env["ir.ui.view"].sudo()
        pattern = re.compile(r"""(name=)(["'])""" + re.escape(old_name) + r"""(\2)""")
        if views is None:
            views = self.dependent_view_ids
        for view in views:
            original = view.arch_db or ""
            replaced, n = pattern.subn(r"\g<1>\g<2>" + new_name + r"\g<3>", original)
            if not n:
                continue
            view.write({"arch_db": replaced})
            try:
                view.with_context(check_view_ids=View.ids).get_combined_arch()
            except Exception as e:
                _logger.error("Rename propagation failed on view %s: %s — rolling back", view.id, e)
                view.write({"arch_db": original})
                raise UserError(_("Rename rolled back: view %s would become invalid (%s).") % (view.name, e)) from e

    # ---------- Apply ----------

    def action_apply(self):
        """Materialise the declared field on the target model via ir.model.fields."""
        IrField = self.env["ir.model.fields"].sudo()
        for rec in self:
            try:
                if rec.ir_model_fields_id:
                    rec.ir_model_fields_id.write(
                        {
                            "field_description": rec.name,
                            "help": rec.help_text,
                            "required": rec.required,
                            "readonly": rec.readonly,
                        }
                    )
                else:
                    vals = rec._build_field_vals()
                    rec.ir_model_fields_id = IrField.create(vals).id
                rec.write({"state": "applied", "last_error": False})
                rec._pdp_audit_write(
                    "studio_field_applied",
                    rec.id,
                    {"model": rec.model_name, "field": rec.technical_name},
                )
            except Exception as e:
                _logger.exception("studio.custom.field %s apply failed", rec.id)
                rec.write({"state": "error", "last_error": str(e)})
                rec._pdp_audit_write("studio_field_apply_failed", rec.id, {"error": str(e)})

    @api.model
    def studio_create_and_place(self, vals, target_view_id, anchor_field, position="after"):
        """Atomic helper for the Studio overlay: create the custom field,
        materialise it via :meth:`action_apply`, then place it on the
        given view (creating/extending the per-view customization).

        Doing all four steps in one transaction avoids the multi-RPC
        race where a second worker would try to validate the
        inheritance against a stale registry that hadn't yet picked up
        the new field.

        ``position`` is honoured if the caller passed one (overlay
        picks "after" the selected field, or "after" the last field).
        Falls back to ``inside`` when no anchor is available.
        """
        rec = self.create([vals])
        rec.action_apply()
        if rec.state != "applied":
            raise UserError(_("Field create failed: %s") % rec.last_error)
        # Flush + refresh so the view validator sees the new column.
        rec.env.flush_all()
        rec.env.registry.signal_changes()
        Cust = self.env["studio.view.customization"].sudo()
        Op = self.env["studio.view.operation"].sudo()
        cust = Cust.search([("target_view_id", "=", target_view_id)], limit=1, order="id desc")
        if not cust:
            cust = Cust.create(
                {
                    "name": f"Studio overlay edits — view {target_view_id}",
                    "target_view_id": target_view_id,
                }
            )
        # The op model requires anchor_field for add_field (see
        # studio.view.operation._check_op). When the overlay couldn't
        # detect a sensible anchor (empty form, drop outside any group)
        # we resolve one here from the combined arch: prefer the last
        # <field> nested inside a <group>, fall back to the last <field>
        # anywhere. If neither exists the view has no usable insertion
        # point and we surface a clear error instead of a ValidationError.
        resolved_anchor = anchor_field or ""
        resolved_position = position or "after"
        if not resolved_anchor:
            try:
                view = self.env["ir.ui.view"].sudo().browse(target_view_id)
                arch = view.get_combined_arch()
                from lxml import etree as _et

                root = _et.fromstring(arch.encode("utf-8") if isinstance(arch, str) else arch)
                group_fields = root.xpath("//group//field[@name]")
                if group_fields:
                    resolved_anchor = group_fields[-1].get("name") or ""
                else:
                    any_fields = root.xpath("//field[@name]")
                    if any_fields:
                        resolved_anchor = any_fields[-1].get("name") or ""
            except Exception:
                _logger.exception("studio_create_and_place: anchor auto-resolve failed for view %s", target_view_id)
        if not resolved_anchor:
            raise UserError(
                _(
                    "Cannot place field '%s' — the target view has no existing field "
                    "to anchor against. Add a field manually first, or open a form "
                    "with at least one group."
                )
                % rec.technical_name
            )
        Op.create(
            {
                "customization_id": cust.id,
                "op_type": "add_field",
                "field_name": rec.technical_name,
                "anchor_field": resolved_anchor,
                "position": resolved_position,
            }
        )
        cust.action_apply()
        return {
            "field_id": rec.id,
            "field_name": rec.technical_name,
            "customization_id": cust.id,
            "customization_state": cust.state,
            "last_error": cust.last_error or "",
        }

    @api.model
    def studio_reset_view(self, target_view_id):
        """Wipe every Studio operation + inheritance for the given view.
        Escape hatch for the overlay so users can recover from
        accumulated bad state without surgical op-by-op cleanup."""
        Cust = self.env["studio.view.customization"].sudo()
        custs = Cust.search([("target_view_id", "=", target_view_id)])
        for c in custs:
            # action_apply with empty ops deactivates + clears the
            # inheritance, so empty out the op list first.
            c.operation_ids.unlink()
            c.action_apply()
        return {"reset_count": len(custs)}

    def _build_field_vals(self) -> dict:
        self.ensure_one()
        vals = {
            "name": self.technical_name,
            "field_description": self.name,
            "model_id": self.model_id.id,
            "ttype": self.field_type,
            "help": self.help_text,
            "required": self.required,
            "readonly": self.readonly,
        }
        if self.field_type == "selection":
            if not self.selection_values:
                raise UserError(_("Selection needs at least one key|label line."))
            pairs = []
            for line in self.selection_values.splitlines():
                if "|" in line:
                    k, v = line.split("|", 1)
                    pairs.append((k.strip(), v.strip()))
            if not pairs:
                raise UserError(_("Selection needs at least one key|label line."))
            vals["selection"] = str(pairs)
        if self.field_type in RELATIONAL_TYPES:
            if not self.relation_model_id:
                raise UserError(_("Relational field requires a target model."))
            vals["relation"] = self.relation_model_id.model
            if self.field_type == "many2many":
                # Default link table name fits inside Postgres' 63-char identifier limit.
                vals["relation_table"] = (
                    self.relation_table or f"x_{self.model_name.replace('.', '_')}_{self.technical_name}_rel"
                )[:63]
                vals["column1"] = f"{self.model_name.replace('.', '_')}_id"[:63]
                vals["column2"] = f"{self.relation_model_id.model.replace('.', '_')}_id"[:63]
            elif self.field_type == "one2many":
                if not self.relation_field_id:
                    raise UserError(_("One2many requires an inverse Many2one field."))
                vals["relation_field"] = self.relation_field_id.name
        return vals
