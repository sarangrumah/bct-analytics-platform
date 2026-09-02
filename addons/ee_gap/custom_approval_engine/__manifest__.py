# -*- coding: utf-8 -*-
{
    "name": "Custom Approval Engine",
    "summary": "Generic multi-tier approval workflow with delegation, OOO, and SLA escalation",
    "description": """
Custom Approval Engine
======================

A model-agnostic approval workflow engine. Attach via the ``approval.mixin``
mixin to any Odoo model (account.move, purchase.order, sale.order, or
custom verticals) and the engine handles:

- **Multi-tier matrices** — define ordered tiers per model + domain condition,
  with per-tier approver resolution (specific users, groups, manager-of-creator,
  or domain-driven).
- **Delegation** — users can designate a stand-in for a defined window.
  Delegates inherit pending approvals for the delegator.
- **OOO (Out-of-Office)** — automatic delegation from approved ``hr.leave``
  records to the leave taker's manager (or explicit fallback).
- **SLA + escalation** — per-tier ``sla_hours`` with three overdue actions:
  auto-approve, escalate-to-next-tier, escalate-to-fallback-user. Cron runs
  every 15 minutes.
- **Audit** — every state change (request, approve, reject, delegate,
  escalate, cancel) writes to ``pdp.audit_log`` via the
  ``custom.pdp.audit.mixin`` chain.
- **Inbox + portal** — approvers see a unified inbox; portal users (external
  approvers) work via a tokenised portal page.
- **Notifications** — mail.thread plus ``custom_ai_bridge`` hook for
  WhatsApp/Telegram (opt-in per tenant).

Integration points shipped: ``account.move`` (post gate), ``purchase.order``
(confirm gate), ``sale.order`` (confirm gate), ``hr.leave`` (OOO source).
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Workflow",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_taxonomy",
        "custom_pdp_audit",
        "mail",
        "hr_holidays",
        "account",
        "purchase",
        "sale",
        "portal",
    ],
    "capability_tags": ["approval-workflow", "audit-trail", "delegation", "sla-escalation"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "security/record_rules.xml",
        "data/ir_cron_data.xml",
        "data/mail_template_data.xml",
        "views/approval_matrix_views.xml",
        "views/approval_request_views.xml",
        "views/approval_delegation_views.xml",
        "views/approval_ooo_views.xml",
        "views/account_move_views.xml",
        "views/purchase_order_views.xml",
        "views/sale_order_views.xml",
        "views/portal_templates.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
