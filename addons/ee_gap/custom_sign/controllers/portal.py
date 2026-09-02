# -*- coding: utf-8 -*-
import base64

from odoo import http
from odoo.http import request


class SignPortal(http.Controller):
    @http.route("/sign/<string:token>", type="http", auth="public", website=True)
    def sign_open(self, token):
        signer = (
            request.env["sign.request.signer"]
            .sudo()
            .search(
                [("access_token", "=", token)],
                limit=1,
            )
        )
        if not signer:
            return request.not_found()
        signer.mark_opened(
            ip=request.httprequest.environ.get("REMOTE_ADDR"),
            ua=request.httprequest.environ.get("HTTP_USER_AGENT"),
        )
        return request.render(
            "custom_sign.sign_page",
            {"signer": signer, "request": signer.request_id},
        )

    @http.route("/sign/<string:token>/submit", type="http", auth="public", website=True, methods=["POST"], csrf=True)
    def sign_submit(self, token, **post):
        signer = (
            request.env["sign.request.signer"]
            .sudo()
            .search(
                [("access_token", "=", token)],
                limit=1,
            )
        )
        if not signer:
            return request.not_found()
        sig_data = post.get("signature_data") or ""
        sig_bytes = None
        if sig_data.startswith("data:image"):
            sig_bytes = base64.b64encode(base64.b64decode(sig_data.split(",", 1)[1]))
        signer.submit_signature(
            signature_data=sig_bytes,
            signature_text=post.get("signature_text"),
        )
        return request.render("custom_sign.sign_done", {"signer": signer})
