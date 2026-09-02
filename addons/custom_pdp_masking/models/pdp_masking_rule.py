# Part of custom_pdp_masking. Licence: LGPL-3.
"""Masking policy: one transform per PDP class, exactly as frozen contract 01 states."""

import logging
import os

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.custom_pdp_core.models.pdp_field_classification import (
    PDP_CLASSES,
    PDP_UI_MASKED_CLASSES,
)

from .pdp_hash import PDP_DIGEST_ALGORITHM, pdp_hmac_sha256

_logger = logging.getLogger(__name__)

#: The four transforms of contract 01. Adding a fifth means the contract changed.
PDP_TRANSFORMS = [
    ("none", "None - value copied verbatim"),
    ("hmac_sha256", "HMAC-SHA256 - deterministic 64-char lowercase hex"),
    (
        "hmac_sha256_or_null",
        "HMAC-SHA256, with drop_to_null columns written as NULL",
    ),
    ("drop", "Dropped at extraction - the column is never selected"),
]

#: ``ir.config_parameter`` fallback key for the tenant salt.
SALT_PARAMETER = "pdp.mask_salt"

#: Environment variable prefix. The tenant is the database name, upper-cased, with every character
#: outside ``[A-Z0-9]`` replaced by ``_``. Database ``erp_dev`` -> ``WAREHOUSE_MASK_SALT_ERP_DEV``.
SALT_ENV_PREFIX = "WAREHOUSE_MASK_SALT_"

#: Last-resort environment variable, used when the tenant-specific one is unset.
SALT_ENV_DEFAULT = "WAREHOUSE_MASK_SALT_DEFAULT"


class PdpMaskingRule(models.Model):
    """One row per PDP class, carrying the transform applied at load."""

    _name = "pdp.masking.rule"
    _description = "PDP Masking Rule"
    _order = "sequence, id"

    name = fields.Char(compute="_compute_name", store=True)
    sequence = fields.Integer(default=10)
    pdp_class = fields.Selection(
        PDP_CLASSES, string="PDP Class", required=True, index=True
    )
    transform = fields.Selection(
        PDP_TRANSFORMS, required=True, default="none"
    )
    ui_masked = fields.Boolean(
        string="Masked in Odoo UI",
        default=False,
        help="When set, users outside PDP / Data Viewer see a masked token instead of the value "
        "in the Odoo web client, for models that inherit pdp.masked.mixin.",
    )
    description = fields.Text()
    active = fields.Boolean(default=True)

    _pdp_class_uniq = models.Constraint(
        "UNIQUE (pdp_class)",
        "A PDP class has exactly one masking rule.",
    )

    @api.depends("pdp_class", "transform")
    def _compute_name(self):
        classes = dict(PDP_CLASSES)
        transforms = dict(PDP_TRANSFORMS)
        for rule in self:
            rule.name = "%s -> %s" % (
                classes.get(rule.pdp_class, rule.pdp_class or ""),
                transforms.get(rule.transform, rule.transform or ""),
            )

    # ------------------------------------------------------------------
    # Salt resolution
    # ------------------------------------------------------------------

    @api.model
    def _tenant_key(self):
        """Return the tenant key used to build the salt environment variable name."""
        name = self.env.cr.dbname or ""
        return "".join(char if char.isalnum() else "_" for char in name).upper()

    @api.model
    def _get_salt(self, raise_if_missing=True):
        """Resolve the per-tenant salt.

        Resolution order, first hit wins:

        1. ``WAREHOUSE_MASK_SALT_<TENANT>`` in the environment (SOPS-injected, per contract 01).
        2. ``WAREHOUSE_MASK_SALT_DEFAULT`` in the environment.
        3. ``ir.config_parameter`` ``pdp.mask_salt`` - for tests and single-tenant dev boxes.

        The salt is never written to a data file and never committed.
        """
        salt = os.environ.get(SALT_ENV_PREFIX + self._tenant_key())
        if not salt:
            salt = os.environ.get(SALT_ENV_DEFAULT)
        if not salt:
            salt = self.env["ir.config_parameter"].sudo().get_param(SALT_PARAMETER)
        if not salt and raise_if_missing:
            raise UserError(
                _(
                    "No PDP masking salt is configured. Set the environment variable %(env)s "
                    "or the system parameter '%(param)s'. Refusing to hash without a salt.",
                    env=SALT_ENV_PREFIX + self._tenant_key(),
                    param=SALT_PARAMETER,
                )
            )
        return salt or ""

    # ------------------------------------------------------------------
    # The transform itself
    # ------------------------------------------------------------------

    @api.model
    def hash_value(self, value, salt=None):
        """Return the deterministic digest of ``value``.

        This is the in-Odoo entry point to the reference implementation. Passing ``salt``
        explicitly is intended for tests and for cross-tenant verification; production callers
        omit it and get the tenant salt.
        """
        return pdp_hmac_sha256(value, salt if salt is not None else self._get_salt())

    @api.model
    @api.readonly
    def get_digest_spec(self):
        """Return the digest construction, so the CDC loader can assert agreement at startup."""
        return {
            "algorithm": PDP_DIGEST_ALGORITHM,
            "primitive": "hmac",
            "digest": "sha256",
            "key": "per-tenant salt (the HMAC key, not a prefix or suffix)",
            "key_encoding": "utf-8",
            "message_encoding": "utf-8",
            "normalisation": "none - no trim, no case fold, no unicode normalisation",
            "output": "lowercase hex, 64 characters",
            "null_in_null_out": True,
            "empty_string_is_null": True,
            "salt_env_var": SALT_ENV_PREFIX + self._tenant_key(),
            "salt_parameter": SALT_PARAMETER,
        }

    @api.model
    @api.readonly
    def get_masking_plan(self, model_names=None):
        """Return the per-column load-time plan the CDC loader executes.

        Shape::

            {
              "res.partner": {
                "email":  {"pdp_class": "personal",  "transform": "hmac_sha256"},
                "comment":{"pdp_class": "sensitive", "transform": "null"},
                "id":     {"pdp_class": "internal",  "transform": "none"}
              }
            }

        ``secret`` columns are omitted entirely rather than given a transform: the loader must not
        be able to name them in a SELECT list at all.
        """
        rules = {rule.pdp_class: rule for rule in self.search([])}
        classification = self.env["pdp.field.classification"]
        payload = classification.get_classification_map(model_names)
        plan = {}
        for model_name, columns in payload["models"].items():
            for field_name, meta in columns.items():
                pdp_class = meta["pdp_class"]
                if pdp_class == "secret":
                    continue
                rule = rules.get(pdp_class)
                transform = rule.transform if rule else "none"
                if transform == "hmac_sha256_or_null":
                    transform = "null" if meta["drop_to_null"] else "hmac_sha256"
                plan.setdefault(model_name, {})[field_name] = {
                    "pdp_class": pdp_class,
                    "transform": transform,
                }
        return plan

    # ------------------------------------------------------------------
    # UI masking support
    # ------------------------------------------------------------------

    @api.model
    def _ui_masked_classes(self):
        """Return the PDP classes flagged for UI masking."""
        classes = set(self.search([("ui_masked", "=", True)]).mapped("pdp_class"))
        # Defensive: never let a data edit turn UI masking off for personal/sensitive entirely.
        return classes or set(PDP_UI_MASKED_CLASSES)

    @api.model
    def _ui_mask_plan(self, model_name):
        """Return ``{column: 'hash' | 'null'}`` for one model's UI-masked columns.

        Only textual columns are listed: masking a boolean or a foreign key would break the client
        without protecting anything the text columns do not already protect.
        """
        model = self.env.get(model_name)
        if model is None:
            return {}
        masked_classes = self._ui_masked_classes()
        rows = self.env["pdp.field.classification"].sudo().search_read(
            [("model_name", "=", model_name), ("pdp_class", "in", list(masked_classes))],
            ["field_name", "pdp_class", "drop_to_null"],
        )
        plan = {}
        for row in rows:
            field = model._fields.get(row["field_name"])
            if field is None or field.type not in ("char", "text", "html"):
                continue
            plan[row["field_name"]] = "null" if row["drop_to_null"] else "hash"
        return plan
