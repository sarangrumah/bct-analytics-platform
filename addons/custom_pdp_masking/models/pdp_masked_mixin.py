# Part of custom_pdp_masking. Licence: LGPL-3.
"""In-Odoo enforcement of the masking policy.

Contract 01 binds the *warehouse* load. This mixin closes the obvious hole next to it: a user who
cannot see personal data in a dashboard but can open the same record in Odoo has not been
restricted at all.

How it works
------------
``read()`` is the single funnel the Odoo 19 web client uses to fetch field values:
``web_read()`` calls ``self.read(fields, load=None)`` and ``web_search_read()`` goes through
``web_read()``. Overriding ``read()`` therefore masks the UI (list, form, kanban, and the
``display_name`` of many2one references pointing at a masked model) without touching any internal
ORM path - ``record.email``, ``mapped()``, ``search()`` domains and ``_compute`` methods all read
through the cache and are unaffected. Business logic keeps working on cleartext; only what is
rendered to a person is masked.

The masked token
----------------
Masked ``char``/``text`` columns become ``***`` plus a short token, so two different partners still
look different in a list and the UI stays navigable. The token is derived from the database UUID,
**not** from the warehouse salt:

* the warehouse salt must never be present in an HTTP response, and
* a UI token must never be mistaken for a warehouse digest and used as a join key.

Columns classified ``sensitive`` with ``drop_to_null`` (free text) are blanked entirely, matching
what the warehouse will hold for them.
"""

import hashlib
import hmac
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

#: Prefix that makes a masked value obvious in a screenshot and greppable in a bug report.
UI_MASK_PREFIX = "***"

#: Number of hex characters of the UI token. Short on purpose: it is a visual discriminator, not
#: a cryptographic identifier.
UI_MASK_TOKEN_LENGTH = 8

#: Replacement for masked free-text columns.
UI_MASK_BLANK = "*** redacted (PDP) ***"


class PdpMaskedMixin(models.AbstractModel):
    """Mask ``personal`` and ``sensitive`` columns in the UI for non-viewers.

    Inherit it into any model that stores personal data::

        class ResPartner(models.Model):
            _name = "res.partner"
            _inherit = ["res.partner", "pdp.masked.mixin"]
    """

    _name = "pdp.masked.mixin"
    _description = "PDP UI Masking Mixin"

    #: Columns this model never masks in the UI, even when classified. Override per model.
    _pdp_ui_mask_exclude = ()

    #: Non-stored companions of a masked column that must be masked with it, otherwise the
    #: cleartext leaks back through a computed label.
    _pdp_ui_mask_companions = {
        "name": ("display_name", "complete_name"),
    }

    @api.model
    def _pdp_ui_may_see_raw(self):
        """Return True when the current user may read personal data unmasked."""
        if self.env.su:
            # Superuser: module installation, data loading, cron and the demo seeder. Masking
            # these would corrupt data rather than protect it.
            return True
        return self.env.user.has_group("custom_pdp_core.group_pdp_data_viewer")

    @api.model
    def _pdp_ui_token(self, value):
        """Return a stable, non-reversible discriminator for ``value``.

        Keyed on the database UUID so it is stable within a database, differs across databases,
        and shares no key material with the warehouse digest.
        """
        key = (
            self.env["ir.config_parameter"].sudo().get_param("database.uuid")
            or self.env.cr.dbname
            or "pdp"
        )
        digest = hmac.new(
            key.encode("utf-8"), str(value).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return UI_MASK_PREFIX + digest[:UI_MASK_TOKEN_LENGTH]

    @api.model
    def _pdp_ui_blank(self):
        """Replacement for a masked free-text column. Shared with the export path."""
        return UI_MASK_BLANK

    def _pdp_ui_mask_value(self, field_name, value, mode):
        if mode == "null":
            return self._pdp_ui_blank()
        return self._pdp_ui_token(value)

    def read(self, fields=None, load="_classic_read"):
        result = super().read(fields=fields, load=load)
        if not result or self._pdp_ui_may_see_raw():
            return result
        plan = self.env["pdp.masking.rule"].sudo()._ui_mask_plan(self._name)
        if not plan:
            return result
        plan = {
            name: mode
            for name, mode in plan.items()
            if name not in self._pdp_ui_mask_exclude
        }
        if not plan:
            return result
        # Companions (display_name, complete_name, ...) inherit the mode of their source column.
        for source, companions in self._pdp_ui_mask_companions.items():
            if source in plan:
                for companion in companions:
                    plan.setdefault(companion, plan[source])
        for row in result:
            for field_name, mode in plan.items():
                if row.get(field_name):
                    row[field_name] = self._pdp_ui_mask_value(
                        field_name, row[field_name], mode
                    )
        return result
