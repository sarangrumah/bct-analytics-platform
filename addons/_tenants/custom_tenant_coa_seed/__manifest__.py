{
    "name": "the tenant Chart of Accounts Seed",
    "version": "19.0.1.0.0",
    "summary": "Tenant-specific CoA, taxes, and fiscal positions for erp_dev_tenant.",
    "description": """
the tenant Chart of Accounts seed.

Loads 548 accounts (10-digit codes) extracted from the tenant Master Data
Template, plus PPN/PPh taxes and Indonesian fiscal positions.

INSTALL ONLY ON THE erp_dev_tenant TENANT DB. The data here is specific to
that tenant and should not be loaded on the generic platform/other tenants.
""",
    "author": "Platform",
    "category": "Tenants/the tenant",
    "depends": [
        "l10n_id_coa_10d",
        "account",
        "base",
        # Product is needed for the product.category default wiring in the
        # post-init hook. If stock is installed in the tenant DB, the hook will
        # also wire stock valuation/input/output accounts; we don't hard-depend
        # on stock_account so the seed remains installable on accounting-only
        # tenants.
        "product",
    ],
    "data": [
        "data/account.fiscal.position.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "auto_install": False,
    "license": "LGPL-3",
}
