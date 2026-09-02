# -*- coding: utf-8 -*-
{
    "name": "Custom Accounting Full",
    "summary": "Indonesian COA + intercompany automation + consolidation reports (CE multi-company gap)",
    "description": """
Custom Accounting Full
======================

Closes the multi-company gap between Odoo CE's base ``account`` module
and the EE ``account_consolidation`` + ``account_inter_company_rules``
modules. Scoped to what an Indonesian SMB-mid group needs:

Features
--------
- **PSAK Chart of Accounts** — provisioned via the
  ``l10n_id_psak_custom`` dependency (53 accounts, 2 PPN taxes, 6
  journals, 2 fiscal positions, all under the ``id_psak`` template
  code exposed in Settings → Accounting → Chart Template).
- **Intercompany automation** — rule table maps a partner (linked to
  ``res.company.partner_id`` of another company) to an auto-mirror
  policy. When Company A posts a sales invoice to a partner that
  represents Company B's commercial entity, Company B automatically
  receives a draft vendor bill mirroring the same lines, with mapped
  accounts and the inter-company journal.
- **Consolidation engine** — declare a parent + N subsidiaries +
  elimination accounts. Wizards produce consolidated Trial Balance,
  P&L, and Balance Sheet across all subsidiaries with a dedicated
  "Eliminations" column.
- **Branch dimension** — ``account.analytic.account`` extended with
  ``branch_code`` + ``is_branch_root`` for cost-centre reporting by
  legal branch / kantor cabang.
- **Unique vendor bill reference** — escalates Odoo CE's soft
  ``duplicated_ref_ids`` warning into a hard block: posting a vendor
  bill whose Bill Reference is already used by the same vendor
  (same company, non-cancelled) raises a ``UserError``. Applies to
  every tenant, since this module ships in all industry packs.

The reports honour the active Odoo user's allowed_company_ids — a
consolidator user must have access to *all* subsidiaries for figures
to be complete; otherwise the report renders only the visible slice
and flags the gap in its header.

Audit
-----
Every consolidation report run, intercompany mirror creation, and
elimination journal entry is written to ``pdp.audit_log`` via the
``pdp.audited.mixin`` chain.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Accounting",
    "version": "19.0.0.4.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_taxonomy",
        "custom_pdp_audit",
        "account",
        "analytic",
        "sale_management",
        "purchase",
        "mail",
        "l10n_id_psak_custom",
    ],
    "capability_tags": ["accounting", "intercompany", "consolidation", "audit-trail", "approval-workflow"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/intercompany_rule_views.xml",
        "views/consolidation_config_views.xml",
        "views/account_analytic_views.xml",
        "views/res_company_views.xml",
        "views/res_config_settings_views.xml",
        "views/consolidation_chart_views.xml",
        "views/elimination_rule_views.xml",
        "views/elimination_proposal_views.xml",
        "views/fiscal_year_views.xml",
        "wizards/consolidation_report_wizard_views.xml",
        "wizards/fiscal_year_close_wizard_views.xml",
        "views/reconcile_rule_views.xml",
        "views/followup_views.xml",
        "views/credit_limit_views.xml",
        "views/match_policy_views.xml",
        "views/match_result_views.xml",
        "reports/consolidation_report_templates.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
