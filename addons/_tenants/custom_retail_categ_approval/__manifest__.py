# -*- coding: utf-8 -*-
{
    "name": "the apparel brand Product Category Change Approval",
    "summary": "Block silent product-category changes and route them through Finance approval",
    "description": """
the apparel brand Product Category Change Approval
=======================================

At the apparel brand a ``product.category`` is an account mapping, not a label: it decides
which ``Gross Sales-<x>``, ``Sales Discount-<x>``, ``Sales Return-<x>``, COGS and
inventory account a product posts to. Changing it on a product that already has
transactions silently misstates the ledger — and nothing already posted follows
the change, because the POS closing entries carry no ``product_id`` at all.

``custom_retail_localization`` already ships **Product Category Reclassification**
(``retail.categ.reclass``), which recomputes the impact and books the correction.
This module makes going through it mandatory, and puts Finance in the loop:

* **Guard** — ``product.template.write`` refuses to change ``categ_id`` on a
  product that has movement *when the change actually moves the GL*. Re-parenting
  inside the same COA bucket (routine in the X101 tree) is untouched.
* **Approval** — ``retail.categ.reclass`` gains ``approval.mixin``. Applying a
  reclassification no longer changes anything directly; it raises a two-tier
  approval request (Accounting Manager, then Finance Manager) and leaves the
  record *Waiting Approval*. The category change and its correction entries are
  produced only once the last tier approves.
* **Notification** — every pending approver gets an Odoo activity naming the
  product, the accounts involved and the amount, and the requester gets a
  pop-up. The approval engine's own mail path is inert (its template was never
  defined), so the activity is what actually reaches people.
* **Imports stay alive** — an MDM feed that would re-categorise an existing
  product no longer aborts the batch, nor does it slip through: the category is
  left as it was and a draft reclassification is raised for review instead.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Accounting",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_retail_localization",
        "custom_approval_engine",
    ],
    "capability_tags": ["approval", "product-category", "governance", "retail"],
    "data": [
        "views/generated_search_views.xml",
        "security/categ_approval_security.xml",
        "security/ir.model.access.csv",
        "data/approval_matrix_data.xml",
        "views/categ_reclass_views.xml",
        "views/product_template_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
