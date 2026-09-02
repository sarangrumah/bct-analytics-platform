# -*- coding: utf-8 -*-
"""Generic duplicate-merge wizard.

For ``res.partner`` we attempt to defer to the standard merge wizard
(``base.partner.merge.automatic.wizard``) when available. For any other
model we perform a generic reassignment: every Many2one column in the
database referencing the duplicate IDs is rewritten to point at the master,
then the duplicates are unlinked. Conflict resolution keeps the master
value and posts an audit message describing what was discarded.
"""

import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CustomDedupMergeWizard(models.TransientModel):
    _name = "custom.dedup.merge.wizard"
    _description = "Dedup Merge Wizard"

    candidate_id = fields.Many2one(
        comodel_name="custom.dedup.candidate",
        string="Candidate",
        required=True,
        ondelete="cascade",
    )
    model_name = fields.Char(
        string="Model",
        related="candidate_id.rule_id.model_name",
        readonly=True,
    )
    record_ids_json = fields.Text(
        string="Record IDs (JSON)",
        readonly=True,
    )
    master_id_int = fields.Integer(
        string="Master Record ID",
        required=True,
        help="Numeric primary key of the record to KEEP. Other duplicates will be merged into it.",
    )
    preview = fields.Char(
        string="Preview",
        related="candidate_id.preview",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        candidate_id = self.env.context.get("default_candidate_id")
        if candidate_id:
            cand = self.env["custom.dedup.candidate"].browse(candidate_id)
            ids = cand._get_record_ids()
            vals.update(
                {
                    "candidate_id": candidate_id,
                    "record_ids_json": json.dumps(ids),
                    "master_id_int": ids[0] if ids else 0,
                }
            )
        return vals

    # ------------------------------------------------------------------
    # Merge logic
    # ------------------------------------------------------------------

    def _merge_partners(self, master_id, dup_ids):
        """Use the standard Odoo partner merge wizard when present."""
        Wiz = self.env.get("base.partner.merge.automatic.wizard")
        if Wiz is None:
            return False
        try:
            wiz = Wiz.sudo().create({})
            # Different Odoo versions accept different signatures
            if hasattr(wiz, "_merge"):
                wiz._merge([master_id] + dup_ids, self.env["res.partner"].browse(master_id))
                return True
        except Exception as exc:  # pragma: no cover — version drift
            _logger.warning("partner merge wizard failed, falling back: %s", exc)
        return False

    def _generic_merge(self, model_name, master_id, dup_ids):
        """Reassign FKs and unlink duplicates, with conflict-aware audit.

        Strategy: iterate over every Many2one column on every installed model;
        for each one whose comodel matches ``model_name``, UPDATE its table
        SET column=master WHERE column IN (dup_ids).
        """
        Model = self.env[model_name].sudo()
        master = Model.browse(master_id)
        if not master.exists():
            raise UserError(_("Master record %d not found in %s.") % (master_id, model_name))
        duplicates = Model.browse(dup_ids).exists()

        # ------- Conflict-aware field merge -------
        conflicts = []
        master_vals = master.read()[0] if master else {}
        for dup in duplicates:
            dup_vals = dup.read()[0]
            for fname, fdef in Model._fields.items():
                if fname in (
                    "id",
                    "create_date",
                    "create_uid",
                    "write_date",
                    "write_uid",
                    "display_name",
                    "__last_update",
                ):
                    continue
                if fdef.type in ("one2many", "many2many"):
                    continue
                if not fdef.store:
                    continue
                m_val = master_vals.get(fname)
                d_val = dup_vals.get(fname)
                if m_val in (None, False, "") and d_val not in (None, False, ""):
                    # Fill empty master from duplicate
                    try:
                        master.write(
                            {
                                fname: d_val
                                if fdef.type != "many2one"
                                else (d_val[0] if isinstance(d_val, (list, tuple)) else d_val)
                            }
                        )
                        master_vals[fname] = d_val
                    except Exception as exc:
                        _logger.debug("merge fill %s skipped: %s", fname, exc)
                elif m_val and d_val and m_val != d_val and fdef.type not in ("binary",):
                    conflicts.append(
                        "%s: kept %r, discarded %r (from id=%d)"
                        % (
                            fname,
                            m_val,
                            d_val,
                            dup.id,
                        )
                    )

        # ------- Reassign FKs across DB -------
        cr = self.env.cr
        for model_key in list(self.env.registry.keys()):
            try:
                model = self.env[model_key]
            except KeyError:
                continue
            if not getattr(model, "_auto", False) or not getattr(model, "_table", None):
                continue
            if getattr(model, "_abstract", False) or getattr(model, "_transient", False):
                continue
            for fname, fdef in model._fields.items():
                if fdef.type != "many2one":
                    continue
                if fdef.comodel_name != model_name:
                    continue
                if not fdef.store:
                    continue
                if fname in ("create_uid", "write_uid"):
                    continue
                column = fdef.name
                table = model._table
                try:
                    cr.execute(
                        'UPDATE "%s" SET "%s" = %%s WHERE "%s" = ANY(%%s)' % (table, column, column),
                        (master_id, list(dup_ids)),
                    )
                except Exception as exc:  # pragma: no cover
                    _logger.warning(
                        "FK reassign skipped on %s.%s: %s",
                        table,
                        column,
                        exc,
                    )
                    cr.rollback()

        # ------- Unlink duplicates -------
        try:
            duplicates.unlink()
        except Exception as exc:
            raise UserError(_("Failed to unlink duplicates: %s") % exc) from exc

        # ------- Audit -------
        if hasattr(master, "message_post"):
            body = _("Merged %(n)d duplicate(s) into this record.") % {"n": len(dup_ids)}
            if conflicts:
                body += "<br/>" + _("Conflicts (master value preserved):") + "<ul>"
                for c in conflicts:
                    body += "<li>%s</li>" % c
                body += "</ul>"
            try:
                master.message_post(body=body)
            except Exception:
                pass
        return True

    def action_merge(self):
        self.ensure_one()
        try:
            all_ids = json.loads(self.record_ids_json or "[]")
        except (TypeError, ValueError):
            raise UserError(_("Invalid record set.")) from None
        master = self.master_id_int
        if master not in all_ids:
            raise UserError(_("Master ID must be one of the duplicate IDs."))
        dup_ids = [i for i in all_ids if i != master]
        if not dup_ids:
            raise UserError(_("Nothing to merge."))

        model_name = self.candidate_id.rule_id.model_name
        if model_name == "res.partner":
            if not self._merge_partners(master, dup_ids):
                self._generic_merge(model_name, master, dup_ids)
        else:
            self._generic_merge(model_name, master, dup_ids)

        self.candidate_id.state = "merged"
        return {"type": "ir.actions.act_window_close"}
