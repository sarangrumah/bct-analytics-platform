# -*- coding: utf-8 -*-
"""WhatsApp message queue with real Meta Cloud API dispatch + inbound storage."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .res_partner import available_phone_search_fields

_logger = logging.getLogger(__name__)


# Map template categories to the consent purpose code that must be active
# on the recipient partner before the message may be sent.
_CATEGORY_PURPOSE = {
    "marketing": "whatsapp_marketing",
    "utility": "whatsapp_utility",
    "authentication": "whatsapp_utility",
}

# Above this count, action_send_bulk dispatches via queue_job instead of
# blocking the calling request.
_BULK_ASYNC_THRESHOLD = 5


def _mask_phone(number: str | None) -> str:
    """Render a recipient number for a log line without disclosing it.

    ``+6281234567890`` -> ``62********890``. Enough to correlate a log entry
    with a record while reading, not enough to contact anyone or to identify
    the subscriber from the log alone.

    No ``+`` is prepended: the input may be in local form (``0812...``) and a
    leading plus would imply a country code that is not there.
    """
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    if not digits:
        return "(none)"
    if len(digits) <= 5:
        return "*" * len(digits)
    return f"{digits[:2]}{'*' * (len(digits) - 5)}{digits[-3:]}"


class WhatsappMessage(models.Model):
    _name = "whatsapp.message"
    _description = "WhatsApp Message"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "create_date desc"

    account_id = fields.Many2one(
        "whatsapp.account",
        string="Account",
        required=True,
        ondelete="restrict",
        index=True,
    )
    template_id = fields.Many2one(
        "whatsapp.template",
        string="Template",
        ondelete="set null",
    )
    to_phone = fields.Char(
        string="To (Phone)",
        required=True,
        help="E.164 formatted recipient number, e.g. +6281234567890.",
    )
    to_partner_id = fields.Many2one(
        "res.partner",
        string="Recipient",
        ondelete="set null",
        index=True,
    )
    body = fields.Text()
    direction = fields.Selection(
        [
            ("outbound", "Outbound"),
            ("inbound", "Inbound"),
        ],
        default="outbound",
        required=True,
        index=True,
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("queued", "Queued"),
            ("sent", "Sent"),
            ("delivered", "Delivered"),
            ("read", "Read"),
            ("failed", "Failed"),
            ("received", "Received"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    error_message = fields.Text(readonly=True)
    sent_at = fields.Datetime(readonly=True)
    provider_message_id = fields.Char(
        string="Provider Message ID",
        readonly=True,
        index=True,
        help="Upstream wamid/message SID returned by the provider on accept.",
    )
    consent_verified = fields.Boolean(default=False, readonly=True)

    # ----- AI draft reply (populated on inbound when account.ai_auto_draft) -----
    ai_draft_text = fields.Text(string="AI Draft Reply", copy=False)
    ai_draft_state = fields.Selection(
        [
            ("none", "No Draft"),
            ("ready", "Draft Ready"),
            ("sent", "Sent"),
            ("dismissed", "Dismissed"),
        ],
        default="none",
        copy=False,
        index=True,
    )
    ai_payload_hash = fields.Char(readonly=True, copy=False)

    # ---------- public API ----------

    def action_send(self):
        """Verify PDP consent, then HTTP-POST to the Meta Cloud API.

        Failures are recorded on the record (state='failed' +
        error_message) and never re-raised to the caller — bulk
        dispatch must keep going if one recipient fails.
        """
        Consent = self.env["pdp.consent"]
        for rec in self:
            if rec.direction == "inbound":
                # Inbound rows are informational only.
                continue

            category = rec.template_id.category if rec.template_id else None
            purpose_code = _CATEGORY_PURPOSE.get(category)

            consent_ok = True
            if purpose_code:
                if not rec.to_partner_id:
                    consent_ok = False
                else:
                    consent_ok = Consent.check_consent(rec.to_partner_id, purpose_code)

            if not consent_ok and category == "marketing":
                raise UserError(
                    _("Cannot send marketing WhatsApp to %s: no active PDP consent for purpose 'whatsapp_marketing'.")
                    % (rec.to_partner_id.display_name or rec.to_phone)
                )

            if not consent_ok:
                _logger.warning(
                    "whatsapp.message %s: sending %s without verified consent (purpose=%s)",
                    rec.id,
                    category,
                    purpose_code,
                )

            rec.consent_verified = consent_ok
            rec._do_send()
        return True

    def action_send_bulk(self):
        """Dispatch many records.

        If the recordset is larger than ``_BULK_ASYNC_THRESHOLD`` and
        ``queue_job`` is available, enqueue one job per message on the
        ``root.whatsapp`` channel. Otherwise send inline.
        """
        if not self:
            return True

        if len(self) <= _BULK_ASYNC_THRESHOLD:
            return self.action_send()

        # queue_job is a hard dependency, so with_delay is always available.
        for rec in self:
            try:
                rec.with_delay(
                    channel="root.whatsapp",
                    description=f"WhatsApp send {rec.id} -> {rec.to_phone}",
                ).action_send()
            except Exception as e:
                # Fall back to inline send if the queue is unavailable.
                _logger.warning(
                    "queue_job dispatch failed for whatsapp.message %s: %s — sending inline",
                    rec.id,
                    e,
                )
                rec.action_send()
        return True

    # ---------- internal: build payload + dispatch ----------

    def _do_send(self):
        """Single-record send. Catches all exceptions and records failure."""
        self.ensure_one()
        account = self.account_id
        if not account.is_active:
            self.write({"state": "failed", "error_message": "Account is inactive."})
            return

        # Sandbox mode: synthesise a message id and short-circuit.
        if account.sandbox_mode:
            fake_id = f"sandbox-{uuid.uuid4().hex[:16]}"
            # The recipient number is masked and the body is reduced to its
            # length: sandbox mode is normally exercised with real customer
            # data during UAT, and Odoo's log is not treated as a sensitive
            # store. The message id and the record are how you trace a send;
            # neither needs the subscriber's number or the text itself.
            _logger.info(
                "[whatsapp sandbox] account=%s msg_id=%s to=%s template=%s body_len=%s -> %s",
                account.name,
                self.id,
                _mask_phone(self.to_phone),
                self.template_id.name if self.template_id else None,
                len(self.body or ""),
                fake_id,
            )
            self.write(
                {
                    "state": "sent",
                    "sent_at": fields.Datetime.now(),
                    "provider_message_id": fake_id,
                    "error_message": False,
                }
            )
            return

        try:
            if account.provider == "baileys":
                payload = self._build_baileys_payload()
                response = account._baileys_post(f"sessions/{account._baileys_session()}/messages", payload)
                provider_id = response.get("id")
                if not provider_id:
                    raise RuntimeError(f"Baileys response missing id: {response!r}")
            else:
                payload = self._build_payload()
                response = account._post("messages", payload)
                messages = response.get("messages") or []
                provider_id = messages[0].get("id") if messages else None
                if not provider_id:
                    raise RuntimeError(f"Meta response missing messages[0].id: {response!r}")
            self.write(
                {
                    "state": "sent",
                    "sent_at": fields.Datetime.now(),
                    "provider_message_id": provider_id,
                    "error_message": False,
                }
            )
        except Exception as e:
            _logger.exception("whatsapp.message %s send failed", self.id)
            self.write(
                {
                    "state": "failed",
                    "error_message": str(e)[:2000],
                }
            )

    def _build_payload(self) -> dict:
        """Return a Meta Cloud API messages payload for this record.

        - If ``template_id`` is set and approved, send as ``template``.
        - Otherwise send as plain ``text``.
        """
        self.ensure_one()
        # Strip leading '+' for Meta E.164 (they accept either, but the
        # docs example omits the plus).
        to = (self.to_phone or "").lstrip("+").strip()

        if self.template_id and self.template_id.status == "approved":
            tpl = self.template_id
            payload = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "template",
                "template": {
                    "name": tpl.name,
                    "language": {"code": tpl.language_code or "id"},
                },
            }
            return payload

        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": self.body or ""},
        }

    def _build_baileys_payload(self) -> dict:
        """Return a Baileys sidecar payload.

        Baileys has no template approval workflow; even if a template
        is attached we just send the resolved body text. Media (image /
        document) is signalled by setting ``body`` to a URL-like string
        is *not* the convention — instead the wizards should set the
        ``attachment_url`` context to switch to ``type='document'``.
        For now this method emits text-only payloads matching the
        sidecar's ``POST /sessions/<id>/messages`` schema.
        """
        self.ensure_one()
        to = (self.to_phone or "").lstrip("+").strip()
        return {
            "to": to,
            "type": "text",
            "text": self.body or "",
        }

    # ---------- webhook update entry-points (called from controller) ----------

    @api.model
    def _apply_status_update(self, status_payload: dict) -> bool:
        """Apply a Meta webhook ``statuses`` entry to a stored message.

        Expected shape per Meta docs::

            {"id": "<wamid>", "status": "delivered"|"read"|"sent"|"failed",
             "timestamp": "...", "errors": [...]}
        """
        wamid = status_payload.get("id")
        status = (status_payload.get("status") or "").lower()
        if not wamid or not status:
            return False

        msg = self.sudo().search([("provider_message_id", "=", wamid)], limit=1)
        if not msg:
            _logger.info("whatsapp webhook: no message for wamid=%s", wamid)
            return False

        # Map Meta lifecycle to our state enum.
        state_map = {
            "sent": "sent",
            "delivered": "delivered",
            "read": "read",
            "failed": "failed",
        }
        new_state = state_map.get(status)
        if not new_state:
            return False

        vals: dict = {"state": new_state}
        if status == "failed":
            errs = status_payload.get("errors") or []
            if errs:
                vals["error_message"] = str(errs[0])[:2000]
        msg.write(vals)
        return True

    @api.model
    def _record_inbound(self, account, message_payload: dict, contact_payload: dict | None = None) -> "WhatsappMessage":
        """Create a whatsapp.message row for an inbound Meta message.

        ``message_payload`` is one entry of ``value.messages``::

            {"from": "62812...", "id": "wamid....", "timestamp": "...",
             "text": {"body": "..."}, "type": "text"}
        """
        from_phone = message_payload.get("from") or ""
        msg_id = message_payload.get("id")
        msg_type = message_payload.get("type") or "text"

        body = ""
        if msg_type == "text":
            body = (message_payload.get("text") or {}).get("body", "")
        else:
            body = f"[{msg_type} message]"

        # Try to resolve the partner by phone. The domain is built from the
        # fields this build actually has: Odoo 19 dropped res.partner.mobile,
        # and naming a missing field in a domain raises ValueError rather than
        # simply not matching -- which took every inbound message down.
        # phone_sanitized is searched first because `phone` keeps whatever the
        # user typed, and a last-9-digits match cannot see past separators.
        partner = self.env["res.partner"]
        tail = from_phone[-9:]
        fields_to_try = available_phone_search_fields(partner)
        if tail and fields_to_try:
            domain = ["|"] * (len(fields_to_try) - 1)
            domain += [(fname, "ilike", tail) for fname in fields_to_try]
            partner = partner.sudo().search(domain, limit=1)

        vals = {
            "account_id": account.id,
            "to_phone": from_phone,
            "to_partner_id": partner.id if partner else False,
            "body": body,
            "direction": "inbound",
            "state": "received",
            "provider_message_id": msg_id,
            "sent_at": fields.Datetime.now(),
        }
        msg = self.sudo().create(vals)

        # Hybrid AI: auto-generate a draft reply on inbound (never auto-sent).
        if account.ai_auto_draft and (account.ai_system_prompt or "").strip():
            try:
                msg.action_generate_ai_draft()
            except Exception as e:
                _logger.warning("whatsapp ai auto-draft failed for msg %s: %s", msg.id, e)

        return msg

    # ---------- AI draft reply ----------

    def _custom_ai_payload(self) -> dict:
        """Build the AI context payload for this inbound message.

        Includes the account persona prompt and the recent message history
        with the same contact (both directions) for conversational context.
        """
        self.ensure_one()
        account = self.account_id
        limit = max(1, account.ai_max_history or 10)
        history_msgs = self.sudo().search(
            [
                ("account_id", "=", account.id),
                ("to_phone", "=", self.to_phone),
                ("id", "!=", self.id),
            ],
            order="create_date desc, id desc",
            limit=limit,
        )
        history = []
        for m in reversed(history_msgs):
            history.append(
                {
                    "direction": m.direction,
                    "date": fields.Datetime.to_string(m.create_date) if m.create_date else "",
                    "body": (m.body or "")[:1000],
                }
            )
        return {
            "system_prompt": account.ai_system_prompt or "",
            "contact_phone": self.to_phone,
            "contact_name": self.to_partner_id.display_name or "",
            "current_message": self.body or "",
            "history": history,
        }

    def _custom_ai_payload_hash(self, payload: dict) -> str:
        try:
            blob = json.dumps(payload, sort_keys=True, default=str)
        except Exception:
            blob = str(payload)
        return hashlib.sha1(blob.encode("utf-8"), usedforsecurity=False).hexdigest()  # nosemgrep

    def action_generate_ai_draft(self):
        """Call the AI gateway to produce a draft reply for this inbound message."""
        self.ensure_one()
        if self.direction != "inbound":
            raise UserError(_("AI draft is only available on inbound messages."))
        account = self.account_id
        if not (account.ai_system_prompt or "").strip():
            raise UserError(_("Set an AI System Prompt on the WhatsApp account first."))

        payload = self._custom_ai_payload()
        payload_hash = self._custom_ai_payload_hash(payload)
        if self.ai_payload_hash == payload_hash and self.ai_draft_text and self.ai_draft_state == "ready":
            return True  # cached — nothing changed

        chat_messages = []
        for entry in payload["history"]:
            role = "assistant" if entry["direction"] == "outbound" else "user"
            chat_messages.append({"role": role, "content": entry["body"]})
        chat_messages.append({"role": "user", "content": payload["current_message"]})

        result = self.env["custom.ai"]._chat(
            messages=chat_messages,
            system=account.ai_system_prompt,
            quality="fast",
            max_tokens=512,
            temperature=0.7,
        )
        text = result.get("response") or result.get("text") or result.get("content") or result.get("summary") or ""
        if not text:
            raise UserError(_("AI gateway returned no text. Raw: %s") % json.dumps(result)[:300])

        self.sudo().write(
            {
                "ai_draft_text": text,
                "ai_draft_state": "ready",
                "ai_payload_hash": payload_hash,
            }
        )
        return True

    def action_regenerate_ai_draft(self):
        """Force a fresh AI call even if the payload hash matches."""
        self.ensure_one()
        self.sudo().write({"ai_payload_hash": False})
        return self.action_generate_ai_draft()

    def action_dismiss_ai_draft(self):
        self.ensure_one()
        self.sudo().write({"ai_draft_text": False, "ai_draft_state": "dismissed"})
        return True

    def action_send_ai_draft(self):
        """Send the AI draft as an outbound reply on the same account."""
        self.ensure_one()
        if self.direction != "inbound":
            raise UserError(_("AI draft send is only available on inbound messages."))
        if not (self.ai_draft_text or "").strip():
            raise UserError(_("No AI draft to send."))

        outbound = self.sudo().create(
            {
                "account_id": self.account_id.id,
                "to_phone": self.to_phone,
                "to_partner_id": self.to_partner_id.id if self.to_partner_id else False,
                "body": self.ai_draft_text,
                "direction": "outbound",
                "state": "draft",
            }
        )
        try:
            outbound.action_send()
        except Exception:
            self.sudo().write({"ai_draft_state": "ready"})  # rollback flag on failure
            raise
        self.sudo().write({"ai_draft_state": "sent"})
        return {
            "type": "ir.actions.act_window",
            "res_model": "whatsapp.message",
            "res_id": outbound.id,
            "view_mode": "form",
            "target": "current",
        }
