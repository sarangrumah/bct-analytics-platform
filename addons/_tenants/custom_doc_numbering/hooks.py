# -*- coding: utf-8 -*-
"""Post-init hook: assign company document codes and build per-company,
monthly-resetting ir.sequence records for the tenant.

Idempotent: re-running (e.g. on module upgrade via a manual call) updates the
existing per-company sequences in place rather than duplicating them.
"""

import logging

_logger = logging.getLogger(__name__)

# Common monthly prefix tail. Uses the matched date range's year/month so the
# printed YYYY/MM always matches the counter that is being reset.
_MONTH_TAIL = "%(range_year)s/%(range_month)s/"

# (sequence code, human name, "<PREFIX>/" head). The head gets the company code
# and the month tail appended -> e.g. "SQ/" -> "SQ/the tenant/2026/06/".
# Note: 'sale.order' (quotation) and 'purchase.order' reuse Odoo's standard
# codes so no python override is needed for those create() flows.
_SEQUENCES = [
    ("sale.order", "the tenant Sales Quotation", "SQ"),
    ("tenant.sale_order", "the tenant Sales Order", "SO"),
    ("purchase.order", "the tenant Purchase Order", "PO"),
    ("custom.bast.document", "the tenant BAST", "BAST"),
]


def _doc_code_for(company):
    """Best-effort mapping of a company to its the tenant/the tenant short code."""
    name = (company.name or "").lower()
    if any(tok in name for tok in ("reksa", "angkasa", "tenant")):
        return "the tenant"
    if any(tok in name for tok in ("inovasi", "media", "aim")):
        return "the tenant"
    return False


def _upsert_sequence(env, code, name, prefix, company):
    Seq = env["ir.sequence"].sudo()
    vals = {
        "name": name,
        "code": code,
        "prefix": prefix,
        "padding": 3,
        "number_increment": 1,
        "implementation": "standard",
        "use_date_range": True,
        "x_monthly_reset": True,
        "company_id": company.id,
    }
    existing = Seq.search([("code", "=", code), ("company_id", "=", company.id)], limit=1)
    if existing:
        existing.write(vals)
        return existing
    return Seq.create(vals)


def _setup_delivery_order(env):
    """Point the tenant outgoing (delivery) picking types at a DO/the tenant/... sequence."""
    aim = env["res.company"].sudo().search([("x_doc_code", "=", "the tenant")], limit=1)
    if not aim:
        return
    seq = _upsert_sequence(
        env,
        "tenant.delivery_order",
        "the tenant Delivery Order",
        "DO/the tenant/" + _MONTH_TAIL,
        aim,
    )
    picking_types = env["stock.picking.type"].sudo().search([("code", "=", "outgoing"), ("company_id", "=", aim.id)])
    if picking_types:
        picking_types.write({"sequence_id": seq.id})
        _logger.info(
            "the tenant numbering: wired %d the tenant delivery picking type(s) to DO sequence",
            len(picking_types),
        )


def post_init_hook(env):
    companies = env["res.company"].sudo().search([])
    configured = 0
    for company in companies:
        code = _doc_code_for(company)
        if not code:
            continue
        company.x_doc_code = code
        for seq_code, name, head in _SEQUENCES:
            prefix = "%s/%s/%s" % (head, code, _MONTH_TAIL)
            _upsert_sequence(env, seq_code, "%s (%s)" % (name, code), prefix, company)
        configured += 1
        _logger.info("the tenant numbering: configured sequences for %s (%s)", company.name, code)

    _setup_delivery_order(env)
    _logger.info("the tenant numbering: post_init_hook done (%d companies)", configured)
