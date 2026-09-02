# -*- coding: utf-8 -*-
import secrets

from . import models
from . import controllers


def _post_init_storefront(env):
    """Generate per-database secrets the storefront needs.

    - The JWT validator's signing secret (HS256) — minted once, never
      shipped to the browser; the BFF only relays tokens Odoo issues.
    - The HMAC shared secret for the ``storefront`` secure_endpoint scope
      used by the Next.js BFF for server-to-server admin calls.
    - A default access-token TTL.

    Idempotent: existing values are left untouched so re-running ``-u`` or
    re-installing never rotates live secrets.
    """
    icp = env["ir.config_parameter"].sudo()

    validator = env["auth.jwt.validator"].sudo().search([("name", "=", "storefront")], limit=1)
    if validator and not validator.secret_key:
        validator.secret_key = secrets.token_urlsafe(48)

    if not icp.get_param("custom_core.secure_endpoint.storefront.secret"):
        icp.set_param("custom_core.secure_endpoint.storefront.secret", secrets.token_hex(32))

    if not icp.get_param("custom_storefront_api.access_ttl"):
        icp.set_param("custom_storefront_api.access_ttl", "900")

    # Enable + publish the native wire-transfer provider so manual bank
    # transfer is available at storefront checkout out of the box (it needs
    # no credentials). Redirect gateways stay disabled until an admin adds
    # their keys. Idempotent: only flips a still-disabled transfer provider.
    transfer = env.ref("payment.payment_provider_transfer", raise_if_not_found=False)
    if transfer and transfer.state == "disabled":
        # write() triggers payment.provider._activate_default_pms(), which
        # activates the linked wire-transfer payment.method (shipped inactive).
        transfer.write({"state": "enabled", "is_published": True})
