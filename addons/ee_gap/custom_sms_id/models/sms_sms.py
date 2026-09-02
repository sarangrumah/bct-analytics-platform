# -*- coding: utf-8 -*-
"""Bridge standard ``sms.sms`` queue to ``custom.sms.account`` adapters.

When an active ``custom.sms.account`` exists for the current company,
the SMS is routed through our adapter (Zenziva / Twilio) instead of
the default Odoo IAP gateway. Falls back to the upstream IAP send
when no account is configured.
"""

from __future__ import annotations

import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class SmsSms(models.Model):
    _inherit = "sms.sms"

    x_custom_account_id = fields.Many2one(
        "custom.sms.account",
        string="Custom SMS Account",
        readonly=True,
        ondelete="set null",
        index=True,
        help="If set, this sms.sms record was dispatched through the "
        "custom adapter instead of Odoo IAP. Populated automatically.",
    )

    # -------- routing --------

    def _resolve_custom_account(self):
        """Return the active custom account for this company, or False."""
        self.ensure_one()
        company = self.env.company
        return (
            self.env["custom.sms.account"]
            .sudo()
            .search(
                [
                    ("is_active", "=", True),
                    ("company_id", "in", (False, company.id)),
                ],
                order="company_id desc, id asc",
                limit=1,
            )
        )

    def _send_via_custom_adapter(self, account):
        """Dispatch a single sms.sms through the custom adapter."""
        self.ensure_one()
        adapter = self.env["custom.sms.adapter.base"]._get_for_account(account)
        try:
            result = adapter.send(
                account,
                self.number or "",
                self.body or "",
                purpose="transactional",
            )
        except Exception:
            _logger.exception("custom_sms_id: adapter send failed for sms.sms %s", self.id)
            self.write(
                {
                    "state": "error",
                    "failure_type": "sms_server",
                    "x_custom_account_id": account.id,
                }
            )
            return False

        if result.get("ok"):
            self.write(
                {
                    "state": "sent",
                    "failure_type": False,
                    "x_custom_account_id": account.id,
                }
            )
            return True
        self.write(
            {
                "state": "error",
                "failure_type": "sms_server",
                "x_custom_account_id": account.id,
            }
        )
        return False

    # -------- override standard send --------

    def _send(self, unlink_failed=False, unlink_sent=True, raise_exception=False):
        """Route through custom adapter when an active account exists.

        Falls back to the upstream IAP-based implementation when no
        ``custom.sms.account`` is configured for this company.
        """
        custom_records = self.browse()
        iap_records = self.browse()
        for sms in self:
            account = sms._resolve_custom_account()
            if account:
                custom_records |= sms
                sms = sms.with_company(account.company_id) if account.company_id else sms
                sms._send_via_custom_adapter(account)
            else:
                iap_records |= sms

        # Honour the standard cleanup semantics on records we handled
        if custom_records:
            sent = custom_records.filtered(lambda r: r.state == "sent")
            failed = custom_records.filtered(lambda r: r.state == "error")
            if unlink_sent and sent:
                sent.sudo().unlink()
            if unlink_failed and failed:
                failed.sudo().unlink()

        if iap_records:
            return super(SmsSms, iap_records)._send(
                unlink_failed=unlink_failed,
                unlink_sent=unlink_sent,
                raise_exception=raise_exception,
            )
        return True
