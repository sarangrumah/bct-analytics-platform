# -*- coding: utf-8 -*-
{
    "name": "Custom eCommerce Indonesia",
    "summary": "Indonesian courier registry (JNE, JNT, SiCepat, AnterAja, Pos) and Midtrans/Xendit checkout link via custom_payment_id",
    "description": """
Custom eCommerce Indonesia extends website_sale + delivery with:
- Courier registry for Indonesian providers (JNE, JNT, SiCepat, AnterAja, Pos Indonesia, Grab, Gojek)
- Per-carrier service type + COD flag + COD ceiling
- Indonesian shipping rate calc on delivery.carrier (mock pricing, RajaOngkir-ready)
- AWB / Resi generation + tracking URL on sale.order
- COD validation against per-carrier ceiling
- Cart abandonment reminder (cron + mail.template)
- Indonesian DJP invoice receipt (qweb)
- Midtrans Snap checkout entry point via custom_payment_id
""",
    "author": "Custom Platform",
    "category": "Websites/eCommerce",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "website_sale",
        "delivery",
        "custom_payment_id",
        "mail",
    ],
    "capability_tags": ["ecommerce", "marketing", "crm", "pdp"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/custom_ecommerce_courier_views.xml",
        "views/sale_order_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
