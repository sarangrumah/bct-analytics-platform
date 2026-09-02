# -*- coding: utf-8 -*-
{
    "name": "Custom Project - VAS PMO REST API",
    "summary": "JWT + HMAC REST surface that the Next.js VAS PMO app runs on.",
    "description": """
VAS PMO - REST API
==================
The read/write surface the headless UI runs on, shaped like ``custom_storefront_api`` so
that the two front-ends are operated the same way.

Auth
----
``auth.jwt.validator`` named ``pmo`` (HS256, ``aud=pmo``). The validator's
``user_id_strategy`` gains a ``pmo_login`` option: unlike the storefront -- where every
caller is a customer and maps to one static internal user -- here the caller *is* an
internal user, and every write has to be attributed to them in the audit trail. The token
therefore carries ``sub`` = login, and ``_get_uid`` resolves the real ``res.users``.

Refresh tokens are stored hashed in ``custom.pmo.token``; the access token lives 15
minutes. ``user_id`` in a request body is never trusted -- identity comes from the token.

Machine-to-machine
------------------
``/pmo/api/hmac/...`` uses the platform HMAC scheme already in use elsewhere:
``X-Signature = HMAC-SHA256(secret, ascii(X-Timestamp) + raw_body)``, five-minute replay
window, secret in ``ir.config_parameter custom_core.secure_endpoint.pmo.secret``.
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Services/Project",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_project_portfolio",
        "custom_project_cr",
        "custom_project_notify",
        "auth_jwt",
    ],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/auth_jwt_validator.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
