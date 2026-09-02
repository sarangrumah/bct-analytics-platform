"""Post-init hook: wire the tenant CoA into module defaults.

Runs once after this addon installs. Sets:
- Company currency (IDR) and fiscal country (Indonesia)
- Forex gain/loss accounts on company
- Default partner receivable/payable via ir.default
- Default product.category income/expense (and stock if available) on root category
- Default journal accounts for existing cash/bank journals
"""

import logging

_logger = logging.getLogger(__name__)


# Role -> external_id (this module's namespace)
ACCOUNT_REFS = {
    "default_receivable": "custom_tenant_coa_seed.account_tenant_1106000001",
    "default_payable": "custom_tenant_coa_seed.account_tenant_2103100001",
    "default_income": "custom_tenant_coa_seed.account_tenant_5199000000",
    "default_expense": "custom_tenant_coa_seed.account_tenant_6199000000",
    "stock_input": "custom_tenant_coa_seed.account_tenant_2103109199",
    "stock_output": "custom_tenant_coa_seed.account_tenant_2103109199",
    "stock_valuation": "custom_tenant_coa_seed.account_tenant_1113100099",
    "forex_gain": "custom_tenant_coa_seed.account_tenant_7607000000",
    "forex_loss": "custom_tenant_coa_seed.account_tenant_7704000000",
    "default_cash": "custom_tenant_coa_seed.account_tenant_1102000001",
    "default_bank": "custom_tenant_coa_seed.account_tenant_1103019300",
}


def _ref(env, xid):
    rec = env.ref(xid, raise_if_not_found=False)
    if not rec:
        _logger.warning("the tenant seed: missing external id %s", xid)
    return rec


def post_init_hook(env):
    company = env.ref("base.main_company", raise_if_not_found=False)
    if not company:
        _logger.warning("the tenant seed: base.main_company not found, skipping wiring")
        return

    accounts = {role: _ref(env, xid) for role, xid in ACCOUNT_REFS.items()}

    # 1. Company-level
    idr = env.ref("base.IDR", raise_if_not_found=False)
    indonesia = env.ref("base.id", raise_if_not_found=False)
    co_vals = {}
    if idr:
        # currency must be active before assigning to company
        if not idr.active:
            idr.active = True
        co_vals["currency_id"] = idr.id
    if indonesia:
        co_vals["country_id"] = indonesia.id
        co_vals["account_fiscal_country_id"] = indonesia.id
    if accounts["forex_gain"]:
        co_vals["income_currency_exchange_account_id"] = accounts["forex_gain"].id
    if accounts["forex_loss"]:
        co_vals["expense_currency_exchange_account_id"] = accounts["forex_loss"].id
    if co_vals:
        company.write(co_vals)
        _logger.info("the tenant seed: company defaults set: %s", list(co_vals))

    # 2. Default partner receivable/payable via ir.default (covers company_dependent fields)
    IrDefault = env["ir.default"].sudo()
    if accounts["default_receivable"]:
        IrDefault.set(
            "res.partner", "property_account_receivable_id", accounts["default_receivable"].id, company_id=company.id
        )
    if accounts["default_payable"]:
        IrDefault.set(
            "res.partner", "property_account_payable_id", accounts["default_payable"].id, company_id=company.id
        )

    # 3. Default product.category accounts on the root "All" category
    Category = env["product.category"]
    # Odoo 19 dropped the product.product_category_all external id; fall back to
    # the topmost parent-less category, or create one if none exist.
    root_cat = env.ref("product.product_category_all", raise_if_not_found=False)
    if not root_cat:
        root_cat = Category.search([("parent_id", "=", False)], limit=1, order="id")
        if not root_cat:
            root_cat = Category.create({"name": "All"})
    cat_vals = {}
    if accounts["default_income"] and "property_account_income_categ_id" in Category._fields:
        cat_vals["property_account_income_categ_id"] = accounts["default_income"].id
    if accounts["default_expense"] and "property_account_expense_categ_id" in Category._fields:
        cat_vals["property_account_expense_categ_id"] = accounts["default_expense"].id
    if "property_stock_account_input_categ_id" in Category._fields:
        if accounts["stock_input"]:
            cat_vals["property_stock_account_input_categ_id"] = accounts["stock_input"].id
        if accounts["stock_output"]:
            cat_vals["property_stock_account_output_categ_id"] = accounts["stock_output"].id
        if accounts["stock_valuation"]:
            cat_vals["property_stock_valuation_account_id"] = accounts["stock_valuation"].id
    if cat_vals and root_cat:
        root_cat.with_company(company).write(cat_vals)
        # Also set defaults for fresh categories created later.
        for fname, val in cat_vals.items():
            IrDefault.set("product.category", fname, val, company_id=company.id)
        _logger.info("the tenant seed: product.category defaults set: %s", list(cat_vals))

    # 4. Default Cash/Bank journal accounts for existing journals lacking one
    Journal = env["account.journal"]
    if accounts["default_cash"]:
        Journal.search(
            [
                ("company_id", "=", company.id),
                ("type", "=", "cash"),
                ("default_account_id", "=", False),
            ]
        ).write({"default_account_id": accounts["default_cash"].id})
    if accounts["default_bank"]:
        Journal.search(
            [
                ("company_id", "=", company.id),
                ("type", "=", "bank"),
                ("default_account_id", "=", False),
            ]
        ).write({"default_account_id": accounts["default_bank"].id})

    _logger.info("the tenant seed: post_init_hook done")
