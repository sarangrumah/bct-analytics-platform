# -*- coding: utf-8 -*-
{
    "name": "Custom POS Indonesia",
    "summary": "Indonesia POS localization: QRIS, rupiah rounding, WhatsApp/SMS e-receipt",
    "description": """
Custom POS Indonesia adds Indonesia-specific extensions to the standard
Point of Sale module:

- QRIS (Quick Response Code Indonesian Standard) payment method metadata
  (provider, merchant ID, static QR, dynamic QR support)
- Rupiah rounding for cash kembalian (none / 50 / 100 / 500 / 1000 IDR)
- Electronic receipt delivery via WhatsApp (custom_whatsapp) and SMS
  (custom_sms_id), with audit tracking on pos.order
""",
    "author": "Custom Platform",
    "category": "Sales/Point of Sale",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "point_of_sale",
        "custom_whatsapp",
        "custom_sms_id",
    ],
    "capability_tags": ["ecommerce", "whatsapp", "indonesian-tax", "pdp"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/pos_payment_method_views.xml",
        "views/pos_config_views.xml",
        "views/pos_order_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
