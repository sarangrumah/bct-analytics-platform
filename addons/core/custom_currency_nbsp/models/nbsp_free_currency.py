# -*- coding: utf-8 -*-
from odoo import models


class NbspFreeCurrency(models.AbstractModel):
    """Marker model — its presence in a registry means "this database opted in".

    Read by ``monkey_patch._enabled`` to keep the process-global patch on
    ``format_amount`` / ``formatLang`` from affecting other tenants served by
    the same worker. It carries no fields and is never instantiated.
    """

    _name = "nbsp.free.currency"
    _description = "Marker: currency strings rendered without non-breaking spaces"
