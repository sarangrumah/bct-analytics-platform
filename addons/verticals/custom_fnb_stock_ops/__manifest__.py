# -*- coding: utf-8 -*-
{
    "name": "Custom F&B Stock Ops (ESB)",
    "summary": "Stock opname, demand forecasting and auto replenishment for F&B outlets running on ESB Core",
    "description": """
Custom F&B Stock Ops
====================

The three capabilities the EFN (the group F&B) vertical needs on top of ESB Core,
built on ``custom_esb_connector``. ESB stays the source of truth for stock: Odoo
runs the intelligence and pushes the outcome back as native ESB documents.

**Stock Opname.** Counting reuses ``custom_wms_cycle_count`` rather than
reimplementing it — the plan/session/line model, the counting UX and the
supervisor variance gate are already there. This module adds the ESB half:
sessions are scoped to an ESB branch + location, expected quantities are seeded
from ``custom.esb.stock.snapshot`` instead of ``stock.quant``, and closing a
session emits **one ESB Item Journal** carrying the signed variance per line.
No Odoo ``stock.move`` is created for an ESB-backed session — that would be a
phantom movement against stock Odoo does not own.

**Demand Forecasting.** Daily material consumption is pulled from the ESB OMS
``get-daily-sales-material-usage`` feed, which is already exploded through the
BOM. Forecasting uses transparent, explainable baselines — moving average,
weighted moving average and day-of-week seasonal naive (F&B demand is strongly
day-of-week driven). No ML dependency: the method is a Selection, so a heavier
model can be swapped in later without changing the consumers.

**Auto Replenishment.** Per-branch rules compute
``forecast x (lead time + review period) + safety stock - on hand - on order``
and raise **draft proposals in Odoo**. Nothing reaches ESB until a user approves;
on approval the proposal becomes an ESB Purchase Request, Goods Transfer Request
or Purchase Order, pushed through the connector's idempotent outbox.

Every cron ships disabled and every push is gated behind ``esb.push_enabled``.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Industry/Food & Beverage",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_esb_connector",
        "custom_wms_cycle_count",
        "queue_job",
    ],
    "capability_tags": ["fnb", "esb-integration", "stock-opname", "demand-forecast", "replenishment"],
    "data": [
        "views/generated_search_views.xml",
        "security/fnb_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "views/cycle_count_esb_views.xml",
        "views/fnb_demand_views.xml",
        "views/fnb_replenishment_views.xml",
        "views/fnb_menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
