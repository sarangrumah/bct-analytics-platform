# Part of custom_pdp_core. Licence: LGPL-3.
"""PDP field classification registry.

Realises frozen contract 01. The five classes below are FROZEN: adding, removing or renaming one
is a breaking change that requires re-briefing every consumer (``custom_pdp_masking``, the CDC
loader and dbt).
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

#: The five frozen PDP classes. Order is significant: least to most restrictive.
PDP_CLASSES = [
    ("public", "Public - non-personal, publishable"),
    ("internal", "Internal - business data, not personal"),
    ("personal", "Personal - UU PDP Art. 4(2) data pribadi umum"),
    ("sensitive", "Sensitive - UU PDP Art. 4(3) data pribadi spesifik"),
    ("secret", "Secret - credentials and key material"),
]

#: Bare class keys, in the same frozen order.
PDP_CLASS_KEYS = tuple(key for key, _label in PDP_CLASSES)

#: Classes whose values must never be shown unmasked outside ``group_pdp_data_viewer``.
PDP_UI_MASKED_CLASSES = ("personal", "sensitive")


class PdpFieldClassification(models.Model):
    """One row per physical database column, carrying its PDP class."""

    _name = "pdp.field.classification"
    _description = "PDP Field Classification"
    _order = "model_name, field_name"
    _rec_names_search = ["model_name", "field_name"]

    model_name = fields.Char(
        string="Model",
        required=True,
        index=True,
        help="Odoo model technical name, e.g. res.partner. Free text on purpose: the registry may "
        "classify a model that is not installed in this database.",
    )
    field_name = fields.Char(
        string="Field",
        required=True,
        index=True,
        help="Physical column name on the model's SQL table.",
    )
    pdp_class = fields.Selection(
        PDP_CLASSES,
        string="PDP Class",
        required=True,
        index=True,
        default="internal",
        help="One of the five frozen classes of contract 01.",
    )
    legal_basis = fields.Char(
        help="Article of UU 27/2022 (or other instrument) justifying this classification.",
    )
    drop_to_null = fields.Boolean(
        string="Drop to NULL",
        default=False,
        help="Only meaningful for the 'sensitive' class. When set, the CDC loader writes NULL "
        "instead of a digest. Set on free-text columns (which can carry anything) and on "
        "non-join-bearing columns (coordinates, blobs) where a digest would be useless.",
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)

    _model_field_uniq = models.Constraint(
        "UNIQUE (model_name, field_name)",
        "A column may carry exactly one PDP classification.",
    )
    _drop_to_null_sensitive_only = models.Constraint(
        "CHECK (drop_to_null IS NOT TRUE OR pdp_class = 'sensitive')",
        "'Drop to NULL' may only be set on a 'sensitive' column - contract 01 defines the "
        "NULL-drop transform for that class only.",
    )

    @api.depends("model_name", "field_name")
    def _compute_display_name(self):
        for record in self:
            record.display_name = "%s.%s" % (record.model_name or "", record.field_name or "")

    @api.constrains("model_name", "field_name")
    def _check_names(self):
        for record in self:
            if not (record.model_name or "").strip():
                raise ValidationError(_("The model name is required."))
            if not (record.field_name or "").strip():
                raise ValidationError(_("The field name is required."))

    # ------------------------------------------------------------------
    # Public API - reachable over JSON-RPC / XML-RPC by the CDC loader.
    # ------------------------------------------------------------------

    @api.model
    @api.readonly
    def get_classification_map(self, model_names=None):
        """Return the full classification map.

        This is *the* method frozen contract 01 promises to the CDC loader.

        :param model_names: optional list of model technical names to restrict the map to.
            ``None`` or an empty list returns every classified model.
        :return: a dict of the shape::

            {
              "contract": "01-classification",
              "module_version": "19.0.1.0.0",
              "classes": ["public", "internal", "personal", "sensitive", "secret"],
              "models": {
                "res.partner": {
                  "email": {
                    "pdp_class": "personal",
                    "drop_to_null": false,
                    "legal_basis": "UU 27/2022 Art. 4(2)",
                    "notes": ""
                  }
                }
              }
            }

        The map contains only active rows. Archiving a row makes the column unclassified, which
        the loader must treat as a hard failure.
        """
        domain = []
        if model_names:
            domain = [("model_name", "in", list(model_names))]
        result = {}
        rows = self.search_read(
            domain,
            ["model_name", "field_name", "pdp_class", "drop_to_null", "legal_basis", "notes"],
            order="model_name, field_name",
        )
        for row in rows:
            result.setdefault(row["model_name"], {})[row["field_name"]] = {
                "pdp_class": row["pdp_class"],
                "drop_to_null": bool(row["drop_to_null"]),
                "legal_basis": row["legal_basis"] or "",
                "notes": row["notes"] or "",
            }
        return {
            "contract": "01-classification",
            "module_version": self._pdp_module_version(),
            "classes": list(PDP_CLASS_KEYS),
            "models": result,
        }

    @api.model
    @api.readonly
    def get_classification(self, model_name, field_name):
        """Return one column's classification, or ``False`` when it is unclassified.

        Returning ``False`` rather than a default class is deliberate: contract 01 forbids a
        silent default to ``public``.
        """
        rows = self.search_read(
            [("model_name", "=", model_name), ("field_name", "=", field_name)],
            ["model_name", "field_name", "pdp_class", "drop_to_null", "legal_basis", "notes"],
            limit=1,
        )
        if not rows:
            return False
        row = rows[0]
        return {
            "model_name": row["model_name"],
            "field_name": row["field_name"],
            "pdp_class": row["pdp_class"],
            "drop_to_null": bool(row["drop_to_null"]),
            "legal_basis": row["legal_basis"] or "",
            "notes": row["notes"] or "",
        }

    @api.model
    @api.readonly
    def get_unclassified_fields(self, model_name, field_names):
        """Return the subset of ``field_names`` carrying no active classification.

        The CDC loader calls this for every table it is about to extract and exits non-zero when
        the returned list is non-empty.
        """
        known = set(self.search([("model_name", "=", model_name)]).mapped("field_name"))
        return [name for name in field_names if name not in known]

    @api.model
    @api.readonly
    def get_extractable_fields(self, model_name):
        """Return the columns the loader may SELECT for ``model_name``.

        ``secret`` columns are excluded structurally: they are never named in the SELECT list, so
        they cannot land in the warehouse even by accident (anti-pattern 7.9).
        """
        rows = self.search_read(
            [("model_name", "=", model_name), ("pdp_class", "!=", "secret")],
            ["field_name"],
            order="field_name",
        )
        return [row["field_name"] for row in rows]

    @api.model
    def _pdp_module_version(self):
        module = self.env["ir.module.module"].sudo().search(
            [("name", "=", "custom_pdp_core")], limit=1
        )
        return module.installed_version or module.latest_version or ""

    # ------------------------------------------------------------------
    # Coverage self-check
    # ------------------------------------------------------------------

    @api.model
    def _pdp_sql_columns(self, model_name):
        """Return the physical Postgres columns backing ``model_name``.

        Returns an empty set when the model is not installed in this database.
        """
        model = self.env.get(model_name)
        if model is None:
            return set()
        self.env.cr.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (model._table,),
        )
        return {row[0] for row in self.env.cr.fetchall()}

    @api.model
    @api.readonly
    def check_coverage(self, model_names):
        """Return ``{model: [unclassified column, ...]}`` for the installed models given.

        Models that are not installed in this database are skipped, not reported as gaps.
        """
        gaps = {}
        for model_name in model_names:
            columns = self._pdp_sql_columns(model_name)
            if not columns:
                continue
            missing = self.get_unclassified_fields(model_name, sorted(columns))
            if missing:
                gaps[model_name] = missing
        return gaps
