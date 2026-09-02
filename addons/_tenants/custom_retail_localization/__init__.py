# -*- coding: utf-8 -*-
from . import models


def post_init_hook(env):
    """Seed the Operating-Unit analytic + per-store journals + account mapping.

    Idempotent; runs only on fresh install. Already-installed the apparel brand DBs are
    provisioned by ``scripts/tenants/retail/40_setup_trade_ou.py``.
    """
    from .models.setup import seed_bank_journals, seed_trade_ou

    seed_trade_ou(env)
    seed_bank_journals(env)
