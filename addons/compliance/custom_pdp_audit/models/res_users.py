# -*- coding: utf-8 -*-
from odoo import models


class ResUsers(models.Model):
    _name = "res.users"
    _inherit = ["res.users", "pdp.audited.mixin"]
