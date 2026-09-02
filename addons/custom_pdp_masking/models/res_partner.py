# Part of custom_pdp_masking. Licence: LGPL-3.
"""Apply the UI masking mixin to ``res.partner``.

``res.partner`` is where the platform's personal data actually lives, so it is the model the
in-Odoo enforcement has to cover.

``res.users`` is deliberately NOT covered
-----------------------------------------
``res.users.login`` is classified ``personal`` and *is* masked in the warehouse. It is not masked
in the Odoo UI, because ``login`` is the identifier the administration screens, the login form and
the "your session" widgets are built around; masking it turns user administration into guesswork
without protecting anything that masking ``res.partner`` does not already protect (the user's name,
e-mail, phone and address are ``res.partner`` columns, reached through ``partner_id``, and those
*are* masked). This is a scope decision, recorded here and in MODULE_KNOWLEDGE.md, not an oversight.
"""

from odoo import models


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "pdp.masked.mixin"]

    #: `ref` is the internal partner code operators type to find a record; masking it makes the
    #: search box useless while the name, contact details and address stay masked.
    _pdp_ui_mask_exclude = ("ref",)
