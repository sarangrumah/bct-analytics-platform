# Part of custom_operating_unit. Licence: LGPL-3.
"""The Operating Unit dimension.

An Operating Unit is a unit of *operation* inside one company: a branch, a depot, a POS cluster, a
regional sales desk. It is not a company and it is not an analytic account:

* a ``res.company`` is a legal entity with its own chart of accounts and its own currency;
* an ``operating.unit`` sits strictly inside exactly one company and never spans two;
* an ``account.analytic.account`` classifies a cost or revenue *line* after the fact, and a line
  can carry several analytic accounts at once. An Operating Unit is a single, mandatory-by-policy
  attribute of the *document*, which is what makes it usable as a warehouse dimension key.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class OperatingUnit(models.Model):
    _name = "operating.unit"
    _description = "Operating Unit"
    _parent_name = "parent_id"
    _parent_store = True
    _order = "complete_name, id"
    _rec_name = "complete_name"
    _rec_names_search = ["complete_name", "code"]

    name = fields.Char(required=True, index=True, translate=False)
    code = fields.Char(
        required=True,
        index=True,
        help="Short stable identifier. The warehouse uses it as the natural key of "
        "dim_operating_unit, so it must not be recycled between units.",
    )
    complete_name = fields.Char(
        compute="_compute_complete_name", recursive=True, store=True, index=True
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
        help="The legal entity this unit operates inside. An Operating Unit never spans "
        "two companies.",
    )
    parent_id = fields.Many2one(
        "operating.unit",
        string="Parent Unit",
        index=True,
        ondelete="restrict",
        domain="[('id', '!=', id)]",
    )
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many("operating.unit", "parent_id", string="Child Units")
    manager_id = fields.Many2one("res.users", string="Manager")
    active = fields.Boolean(default=True)

    _code_company_uniq = models.Constraint(
        "UNIQUE (code, company_id)",
        "An Operating Unit code must be unique within its company.",
    )

    @api.depends("name", "parent_id.complete_name")
    def _compute_complete_name(self):
        for unit in self:
            if unit.parent_id:
                unit.complete_name = "%s / %s" % (unit.parent_id.complete_name, unit.name)
            else:
                unit.complete_name = unit.name

    @api.constrains("parent_id")
    def _check_parent_recursion(self):
        if not self._check_recursion():
            raise ValidationError(_("An Operating Unit cannot be its own ancestor."))

    @api.constrains("parent_id", "company_id")
    def _check_parent_company(self):
        for unit in self:
            if unit.parent_id and unit.parent_id.company_id != unit.company_id:
                raise ValidationError(
                    _(
                        "Operating Unit %(child)s belongs to company %(child_company)s but its "
                        "parent %(parent)s belongs to %(parent_company)s. A unit hierarchy stays "
                        "inside one company.",
                        child=unit.name,
                        child_company=unit.company_id.display_name,
                        parent=unit.parent_id.name,
                        parent_company=unit.parent_id.company_id.display_name,
                    )
                )
