# Part of custom_operating_unit. Licence: LGPL-3.
"""Per-user Operating Unit entitlement.

``allowed_operating_unit_ids`` is the field frozen contract 02 reads to fill the ``allowed_ou``
JWT claim. The name is part of that contract; renaming it breaks the login-gateway.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResUsers(models.Model):
    _inherit = "res.users"

    allowed_operating_unit_ids = fields.Many2many(
        "operating.unit",
        "operating_unit_res_users_rel",
        "user_id",
        "operating_unit_id",
        string="Allowed Operating Units",
        help="Operating Units this user may read documents from. Empty means the user sees only "
        "documents that carry no Operating Unit - the rules fail closed, not open.",
    )
    default_operating_unit_id = fields.Many2one(
        "operating.unit",
        string="Default Operating Unit",
        help="Pre-filled on new sales orders, invoices, transfers, POS orders and PPOB "
        "transactions.",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        # Lets a user see their own entitlement without granting Settings access.
        return super().SELF_READABLE_FIELDS + [
            "allowed_operating_unit_ids",
            "default_operating_unit_id",
        ]

    @api.constrains("default_operating_unit_id", "allowed_operating_unit_ids")
    def _check_default_operating_unit(self):
        for user in self:
            default = user.default_operating_unit_id
            if default and user.allowed_operating_unit_ids and default not in user.allowed_operating_unit_ids:
                raise ValidationError(
                    _(
                        "The default Operating Unit %(unit)s is not in the allowed list of user "
                        "%(user)s.",
                        unit=default.display_name,
                        user=user.name,
                    )
                )

    def _pdp_allowed_operating_unit_ids(self):
        """Return the ids the ``allowed_ou`` JWT claim is built from.

        A stable, documented entry point so the login-gateway does not have to know whether the
        entitlement is stored directly or derived.
        """
        self.ensure_one()
        return self.allowed_operating_unit_ids.ids
