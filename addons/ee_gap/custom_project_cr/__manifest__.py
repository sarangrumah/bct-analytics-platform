# -*- coding: utf-8 -*-
{
    "name": "Custom Project - Change Requests (VAS)",
    "summary": "Change Request as its own record: intake triage, impact analysis, tiered "
    "approval, official numbering, and the tasks it spawns.",
    "description": """
VAS PMO - Change Requests
=========================
A change request is **not** a task with a different label, so it is not modelled as one.

Three things a task does not have, and which would otherwise sit empty on every task in
the system:

1. **A tiered approval gate.** BA -> Product Owner -> vertical owner (the third tier only
   for high / critical impact), each decision recorded with who and when.
2. **Impact analysis.** Affected modules, risk, effort, downtime need, rollback plan.
3. **Its own response SLA**, counted from the moment the brand asked -- not from the
   moment someone started working -- plus an official ``CR-YYYY-NNNN`` number the brand
   quotes back at us.

What *is* shared with tasks: the stage set and its SLA-clock semantics
(``project.task.type`` from ``custom_project_portfolio``) and the audit trail. Behaviour
that must feel identical is implemented once.

Intake
------
New requests land in a single triage queue (``approval_state = draft``) exactly like
Plane's Intake: one queue, one reviewer decision, then it either becomes analysed work or
is rejected with a reason.

Deviation from the plan
-----------------------
The plan said to drive approvals through ``custom_approval_engine``. This module keeps a
small native approval chain instead, so that a change request stays installable and
testable without pulling the whole approval stack into this tenant. The hook
``_cr_external_approval_hook`` is where that integration lands when it is wanted.
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Services/Project",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_project_portfolio",
    ],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/cr_sequence.xml",
        "views/custom_change_request_views.xml",
        "views/cr_menus.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
