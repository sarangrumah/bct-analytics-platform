# -*- coding: utf-8 -*-
{
    "name": "Custom PPOB - SLA Targets & Throughput",
    "summary": "Declarative per-provider/class throughput + latency targets, and "
    "hourly throughput sampling that holds both the Oracle historical "
    "baseline and Odoo actuals for parallel-run parity.",
    "description": """
Custom PPOB Suite - SLA Targets & Throughput
============================================
Closes open decision **D4** (target throughput & H2H SLA) by making it
configuration rather than a design-time constant, and by shipping the
measurement needed to ever verify it.

Two models:

``custom.ppob.sla.target``
    Declarative targets scoped by **provider x product class**, with wildcard
    fallback (empty provider and/or class matches anything). Resolution picks the
    most specific row: ``(provider, class) > (provider, *) > (*, class) > (*, *)``.
    A single wildcard baseline row is seeded at install, derived from the
    indicative 10k-50k txn/day in the v2 realignment brief. The peak-TPS
    derivation is NOT a hidden constant: ``active_hours`` and ``peak_factor`` are
    visible, editable fields, and ``peak_tps_target`` is computed from them yet
    remains manually overridable.

    ``calibration_source`` tracks how much a row can be trusted --
    ``default_baseline`` (a guess) -> ``oracle_historical`` (measured from the
    legacy MSG016T history) -> ``measured`` (measured from Odoo itself). Nothing
    silently promotes a guess to a fact.

``custom.ppob.throughput.sample``
    Hourly buckets per provider x class: txn count, true peak TPS (max
    transactions in any single second of the hour), avg + p95 adapter latency,
    success rate, gross amount. Each row carries ``source`` = ``odoo`` or
    ``oracle``, so the SAME table holds the legacy baseline and the new actuals.

    That is the point: once Oracle history is imported (``source=oracle``) and
    Odoo starts sampling itself (``source=odoo``), the parallel-run parity check
    of WS-8 is one pivot on one model, not a bespoke comparison harness.

Ready-to-go-live posture
------------------------
Install now, before any transaction exists and before Oracle access lands: the
baseline target row is seeded, the hourly cron runs and simply records nothing
while there is no traffic, and the dashboards render empty. When Oracle access
arrives, import history to ``source=oracle`` and recalibrate the targets.

Measurement caveat
------------------
``peak_tps`` and latency are sampled from ``custom.ppob.transaction`` via raw
SQL, which is flushed (``env.flush_all()``) before reading so that transactions
posted earlier in the same cursor are not missed. Latency comes from
``provider_latency_ms`` (adapter RTT, added by ``custom_ppob_sale``) and is
therefore available on every adapter -- including ``ppob_mock`` and
``ppob_oracle_bridge``, which write no ``custom.adapter.call.log`` row at all.
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Industry/PPOB",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_ppob_sale",
    ],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/cron_sla.xml",
        "data/sla_target_baseline.xml",
        "views/ppob_sla_target_views.xml",
        "views/ppob_throughput_sample_views.xml",
        "views/menu_views.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
