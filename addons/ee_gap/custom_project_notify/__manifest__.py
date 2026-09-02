# -*- coding: utf-8 -*-
{
    "name": "Custom Project - VAS Notifications (WA + Email + Odoo)",
    "summary": "Rule-driven notifications for project / CR / task / weekly events, queued in "
    "Odoo and dispatched to the Next.js BFF over HMAC for WhatsApp + e-mail.",
    "description": """
VAS PMO - Notifications
=======================
Why the event is born in Odoo, not in the BFF
---------------------------------------------
e-Telekomunikasi puts its notification calls straight in the Next.js route handlers,
which works there because Next.js is the only writer. Here there are four: the VAS PMO
UI, the Odoo backend, the Jira webhook, and the ticket bridge. Wiring notifications into
the BFF alone would leave three of those four silent.

So ``project.task`` / ``custom.change.request`` / ``project.project`` /
``custom.weekly.progress`` write a row into ``custom.project.notify.outbox`` on every
tracked event, whatever the origin. A cron drains the outbox to the BFF over HMAC, and
the BFF renders and sends -- so the actual WhatsApp / e-mail code stays a single
TypeScript file that mirrors ``notification-service.ts``, while the coverage is complete.

Rules are data
--------------
``custom.project.notify.rule`` maps event -> recipient kinds -> channels. The PO Lead can
change who hears about what without a deploy. That is a direct lift of e-Telekomunikasi's
``DOC_NEXT_REVIEWER`` map, promoted from a Python constant to a table.

Three logs, deliberately not merged
-----------------------------------
* ``pdp.audit.log`` answers "who changed what".
* ``custom.project.notify.log`` (here) answers "was the human actually told".
* ``adapter.call.log`` answers "did the outside world respond".
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Services/Project",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_project_portfolio",
        "custom_project_cr",
    ],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/notify_rule_data.xml",
        "data/notify_cron.xml",
        "views/notify_rule_views.xml",
        "views/notify_outbox_views.xml",
        "views/notify_log_views.xml",
        "views/notify_menus.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
