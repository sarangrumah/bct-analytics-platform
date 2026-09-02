# -*- coding: utf-8 -*-
"""Shared notification behaviour for anything that can raise a VAS event.

Concrete models only have to answer one question -- "who counts as the assignee / the PO /
the brand PIC for me" -- and the mixin does the rest: read the rules, de-duplicate people,
handle the Odoo channel inline, and queue WhatsApp + e-mail for the BFF.
"""

import logging

from odoo import _, api, models

_logger = logging.getLogger(__name__)


class PmoNotifySource(models.AbstractModel):
    _name = "pmo.notify.source"
    _description = "VAS Notification Source Mixin"

    # ------------------------------------------------------------------
    # To be provided by the concrete model
    # ------------------------------------------------------------------

    def _pmo_recipient_map(self):
        """Return ``{recipient_kind: recordset}``.

        Values may be ``res.users`` or ``res.partner`` recordsets -- both are normalised
        into plain dicts by ``_pmo_contact``.
        """
        self.ensure_one()
        return {}

    def _pmo_event_context(self, event):
        """Extra payload fields the message templates want. Optional."""
        self.ensure_one()
        return {}

    # ------------------------------------------------------------------
    # Contact normalisation
    # ------------------------------------------------------------------

    @api.model
    def _pmo_contact(self, record, kind):
        """Normalise a user or partner into a recipient dict."""
        if not record:
            return None
        if record._name == "res.users":
            partner = record.partner_id
            name = record.name
            email = record.email or partner.email
        else:
            partner = record
            name = record.name
            email = record.email
        # Odoo 19 merged res.partner.mobile into phone. Probed rather than assumed so
        # this keeps working on a tenant still on an older release.
        phone = False
        if partner:
            phone = partner.phone or (partner.mobile if "mobile" in partner._fields else False)
        if not email and not phone:
            return None
        return {
            "kind": kind,
            "name": name,
            "email": email or "",
            "phone": phone or "",
            "partner_id": partner.id if partner else False,
            "user_id": record.id if record._name == "res.users" else False,
        }

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _pmo_dispatch(self, event, extra=None):
        """Resolve rules for ``event`` and hand the work off. Never raises."""
        self.ensure_one()
        try:
            rules = self.env["custom.project.notify.rule"].sudo().rules_for(event)
            if not rules:
                return False

            recipient_map = self._pmo_recipient_map()
            wanted_wa_email = {}
            odoo_partners = self.env["res.partner"]

            for rule in rules:
                targets = self._pmo_rule_targets(rule, recipient_map)
                for target in targets:
                    contact = self._pmo_contact(target, rule.recipient_kind)
                    if rule.channel_odoo:
                        partner = target.partner_id if target._name == "res.users" else target
                        odoo_partners |= partner
                    if not contact:
                        continue
                    if not (rule.channel_wa or rule.channel_email):
                        continue
                    key = (contact["partner_id"], contact["kind"])
                    merged = wanted_wa_email.setdefault(key, dict(contact))
                    merged["wa"] = merged.get("wa") or rule.channel_wa
                    merged["email_enabled"] = merged.get("email_enabled") or rule.channel_email

            # Odoo channel is handled here and now -- it is in-database, so there is no
            # reason to send it on a round trip through the BFF.
            if odoo_partners:
                self._pmo_post_odoo(event, odoo_partners)

            recipients = list(wanted_wa_email.values())
            wanted_external = any(rule.channel_wa or rule.channel_email for rule in rules)
            if recipients or wanted_external:
                # Called even with an empty recipient list on purpose: "the rules matched
                # but nobody was reachable" is a finding worth recording, and enqueue()
                # writes exactly that into the delivery log.
                context = dict(self._pmo_event_context(event) or {})
                context.update(extra or {})
                self.env["custom.project.notify.outbox"].sudo().enqueue(
                    self,
                    event,
                    recipients,
                    extra=context,
                )
            return True
        except Exception:  # noqa: BLE001 - notification must never break the transaction
            _logger.exception(
                "VAS PMO: failed to queue notification %s for %s,%s",
                event,
                self._name,
                self.id,
            )
            return False

    def _pmo_rule_targets(self, rule, recipient_map):
        if rule.recipient_kind == "group":
            if not rule.role_group_id:
                return self.env["res.users"]
            return rule.role_group_id.sudo().user_ids
        return recipient_map.get(rule.recipient_kind) or self.env["res.users"]

    def _pmo_post_odoo(self, event, partners):
        """Odoo inbox channel: a chatter message, and an activity when action is needed."""
        self.ensure_one()
        if not hasattr(self, "message_post"):
            return
        label = dict(self.env["custom.project.notify.rule"]._fields["event"].selection).get(event, event)
        self.message_post(
            body=_("<p><b>%(label)s</b> — %(name)s</p>", label=label, name=self.display_name),
            partner_ids=partners.ids,
            subtype_xmlid="mail.mt_note",
        )
        if event in ("verify_request", "overdue", "escalation", "cr_submit", "hold_expired"):
            for partner in partners:
                user = self.env["res.users"].sudo().search([("partner_id", "=", partner.id)], limit=1)
                if not user:
                    continue
                try:
                    self.activity_schedule(
                        "mail.mail_activity_data_todo",
                        summary=label,
                        user_id=user.id,
                    )
                except Exception:  # noqa: BLE001
                    _logger.debug("VAS PMO: could not schedule activity on %s", self._name)
