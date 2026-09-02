# -*- coding: utf-8 -*-
{
    "name": "Custom Storefront API",
    "summary": "Headless JSON REST API for the Next.js storefront (catalog, cart, JWT auth, checkout, payment)",
    "description": """
Custom Storefront API exposes a headless commerce surface so an external
Next.js storefront can drive Odoo as the e-commerce + FICO backend:

- Public catalog (categories, product listing/PLP, product detail/PDP) and
  shipping rate quotes — ``auth=public`` + CORS.
- Customer auth (register/login/refresh/logout) issuing short-lived JWT
  access tokens (via the vendored ``auth_jwt``) + rotating opaque refresh
  tokens stored hashed in ``custom.storefront.token``.
- Cart (reuses the ``website_sale`` draft ``sale.order``), addresses,
  shipping selection, checkout (``action_confirm``), order history, and
  payment initiation through ``custom_payment_id`` adapters.
- A server-to-server admin surface guarded by the ``custom_core``
  HMAC ``@secure_endpoint('storefront')`` decorator for the Next.js BFF.

Design: plain ``@http.route(type='http')`` controllers returning JSON via
``request.make_json_response`` (consistent with custom_hht_bridge and the
custom_payment_id webhooks), with explicit CORS handling.
""",
    "author": "Custom Platform",
    "category": "Websites/eCommerce",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_ecommerce",
        "custom_payment_id",
        "payment_custom",
        "website_sale",
        "sale",
        "auth_jwt",
    ],
    "capability_tags": ["ecommerce", "headless-api", "jwt", "cors"],
    "data": [
        "security/ir.model.access.csv",
        "data/auth_jwt_validator.xml",
        "data/ir_config_parameter.xml",
        "data/headless_disable_website_editor.xml",
        "views/storefront_views.xml",
        "views/payment_proof_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "post_init_hook": "_post_init_storefront",
    "installable": True,
    "application": True,
    "auto_install": False,
}
