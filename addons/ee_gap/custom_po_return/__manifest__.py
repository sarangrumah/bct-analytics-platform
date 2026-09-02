# -*- coding: utf-8 -*-
{
    "name": "Custom PO Return",
    "summary": "Quantity-driven vendor returns with FIFO allocation across POs/GRs and auto credit notes",
    "description": """
Vendor return (retur pembelian / RTV) driven by total quantity instead of per-GR picking.

Provides:

- `custom.po.return`: return document per supplier with multiple product lines.
- FIFO allocation engine: the system consumes the oldest purchase orders first,
  each slice priced at the original PO unit price, and records which goods
  receipt (GR) and which vendor bill back every slice.
- Stock returns created through the standard `stock.return.picking` wizard
  (one return picking per source GR) so chain links, `qty_received` decrement
  and valuation follow core behaviour.
- Draft vendor credit notes grouped per original vendor bill (plus one
  standalone credit note for slices whose PO has no posted bill yet).
- Over-return protection: returnable quantity per PO accounts for native Odoo
  returns and pending allocations from other PO Return documents.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Inventory/Purchase",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": ["purchase_stock", "stock_account", "account", "mail"],
    "capability_tags": ["purchase", "inventory", "vendor-return"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "views/po_return_views.xml",
        "views/purchase_order_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
