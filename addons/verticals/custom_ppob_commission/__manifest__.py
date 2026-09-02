# -*- coding: utf-8 -*-
{
    "name": "Custom PPOB - Commission",
    "summary": "Two-way PPOB commissions: provider->us income and us->mitra "
    "rebate with PPh 23 withholding via the platform engine.",
    "description": """
Custom PPOB Suite - Commission
==============================
Two-way commissions:

* provider_to_us: rebate earned from the provider. Accrued to Commission
  Receivable on each success transaction; settled against a provider vendor
  refund at period close.
* us_to_mitra: rebate paid to mitra. Accrued to Mitra Rebate Expense +
  Commission Payable; settled with PPh 23 withholding resolved through
  ``custom.witholding.engine`` (custom_pph_witholding) -- 2% with NPWP / 4%
  without, per the platform's rate matrix.

On each successful transaction, matching rules create ``custom.ppob.commission.accrual``
rows; the settlement wizard closes a period into vendor refunds or mitra payouts.
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Industry/PPOB",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_ppob_sale",
        "custom_pph_witholding",
        # custom_pph_witholding.custom.witholding.application declares a
        # bupot_line_id -> custom.bupot.unifikasi.line M2O but does not depend
        # on the module that defines it; pull it in so PPh-23 commission
        # withholding integrates with e-Bupot Unifikasi.
        "custom_coretax_bupot",
    ],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "wizard/ppob_commission_settlement_wizard_views.xml",
        "views/ppob_commission_rule_views.xml",
        "views/ppob_commission_accrual_views.xml",
        "views/menu_views.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
