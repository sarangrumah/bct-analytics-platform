# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import api, models

from ..monkey_patch import normalize


class IrQwebFieldMonetary(models.AbstractModel):
    """QWeb ``monetary`` widget — the amounts printed on every PDF/HTML report.

    ``ir.qweb.field.monetary.value_to_html`` builds its own string rather than
    calling ``format_amount``, so patching the tools helper does not reach it;
    it needs its own override. Reached only on databases where this addon is
    installed, which is exactly the scoping we want.
    """

    _inherit = "ir.qweb.field.monetary"

    @api.model
    def value_to_html(self, value, options):
        result = super().value_to_html(value, options)
        # ``label_price`` returns Markup wrapping <span> elements; normalising
        # whitespace introduces no markup, so re-wrapping is safe.
        if isinstance(result, Markup):
            # noqa: S704 - re-wrapping content that was ALREADY Markup. `normalize`
            # only substitutes whitespace characters and cannot introduce a tag, so
            # this neither adds markup nor removes escaping the super() call applied.
            return Markup(normalize(str(result)))  # noqa: S704
        return normalize(result)
