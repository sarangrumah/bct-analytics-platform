# -*- coding: utf-8 -*-
# License: LGPL-3
"""Serve the WMS handheld shell at the same /hht/ URL the bridge uses.

Operators keep one bookmark and one installed PWA. Databases without this
module (the tenant production) keep the bridge's generic shell untouched, because
the override only exists where this module is installed.
"""

from __future__ import annotations

import json

from odoo import http
from odoo.http import request

from odoo.addons.custom_hht_bridge.controllers.pwa_shell import HhtPwaShell


class WmsHhtShell(HhtPwaShell):
    @http.route("/hht/", type="http", auth="user", methods=["GET"], csrf=False)
    def hht_shell(self, **_kw):
        return request.render(
            "custom_wms_hht.wms_hht_shell_layout",
            {
                "session_info": request.env["ir.http"].session_info(),
                "debug": request.session.debug,
                "json": json,
            },
        )
