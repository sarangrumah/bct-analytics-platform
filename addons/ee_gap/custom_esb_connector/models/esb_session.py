# -*- coding: utf-8 -*-
"""ESB authentication session.

ESB Core issues a 1-hour access token and a 24-hour refresh token, and the docs
are explicit that *"a successful API login will log you out of any existing ESB
Core session using the same credentials, vice versa"*. That single sentence
drives this whole model:

- exactly **one** session record per adapter config, holding the shared token;
- rotation is serialised behind a ``SELECT ... FOR UPDATE`` row lock, so two
  Odoo workers hitting an expired token cannot log in concurrently and evict
  each other;
- the token is refreshed ``SKEW_S`` before expiry rather than after a 401.

If the ESB PIC issues a **static API key** instead, set ``auth_mode = static``
and the whole rotation problem disappears — the key is read straight from
``ir.config_parameter``.

The password/API key is *never* stored on this record. The record holds a
``credential_ref`` key name and the value lives in ``ir.config_parameter``,
matching ``custom.adapter.config.credential_ref``.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

#: Documented token lifetimes. ESB returns no expires_in, so we derive them.
ACCESS_TTL_S = 3600
REFRESH_TTL_S = 86400
#: Refresh this long before expiry instead of racing the clock.
SKEW_S = 300


class EsbSession(models.Model):
    _name = "custom.esb.session"
    _description = "ESB API Session"
    _inherit = ["pdp.audited.mixin"]
    _order = "adapter_config_id"

    name = fields.Char(compute="_compute_name", store=True)
    adapter_config_id = fields.Many2one(
        "custom.adapter.config",
        required=True,
        ondelete="cascade",
        index=True,
        help="The ESB host this session authenticates against.",
    )
    auth_mode = fields.Selection(
        [("jwt", "Login + Refresh Token"), ("static", "Static API Key")],
        default="jwt",
        required=True,
        help="Static API keys are preferred: they remove token rotation and the "
        "single-session eviction problem entirely.",
    )
    username = fields.Char(help="ESB user dedicated to this integration. Never a human's account.")
    credential_ref = fields.Char(
        string="Credential ir.config_parameter Key",
        help="Key in ir.config_parameter holding the ESB password (jwt mode) or "
        "the API key (static mode). The secret itself is never stored here.",
    )

    access_token = fields.Text(readonly=True, groups="custom_esb_connector.group_esb_admin")
    refresh_token = fields.Text(readonly=True, groups="custom_esb_connector.group_esb_admin")
    access_expires_at = fields.Datetime(readonly=True)
    refresh_expires_at = fields.Datetime(readonly=True)
    last_login_at = fields.Datetime(readonly=True)
    login_count = fields.Integer(readonly=True, default=0)
    last_error = fields.Char(readonly=True)

    company_code = fields.Char(readonly=True, help="companyCode returned by ESB at login.")
    company_id_esb = fields.Integer(string="ESB Company ID", readonly=True)

    # Odoo 19 silently ignores _sql_constraints — models.Constraint is the live form.
    _adapter_uniq = models.Constraint(
        "unique(adapter_config_id)", "There can only be one ESB session per adapter config."
    )

    @api.depends("adapter_config_id.name")
    def _compute_name(self):
        for rec in self:
            rec.name = rec.adapter_config_id.name or "ESB session"

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    @api.model
    def _for_config(self, config):
        """Return the session for an adapter config, creating it lazily.

        All three ESB hosts share one set of credentials, so a host without its
        own session record falls back to the Core session rather than forcing
        the operator to configure the same credentials three times.
        """
        if not config:
            return self.browse()
        session = self.sudo().search([("adapter_config_id", "=", config.id)], limit=1)
        if session:
            return session
        core = self.sudo().search([("username", "!=", False)], limit=1)
        return core

    # ------------------------------------------------------------------
    # Token lifecycle
    # ------------------------------------------------------------------

    def _get_credential(self) -> str:
        self.ensure_one()
        if not self.credential_ref:
            return ""
        return self.env["ir.config_parameter"].sudo().get_param(self.credential_ref, "") or ""

    def _ensure_token(self) -> str:
        """Return a usable access token, refreshing or logging in if needed.

        Serialised on the session row: concurrent workers queue on the lock, and
        whoever gets in second re-reads the row and finds a fresh token, so only
        one login actually happens.
        """
        self.ensure_one()
        if self.auth_mode == "static":
            return self._get_credential()

        self._lock()
        now = fields.Datetime.now()
        if self.access_token and self.access_expires_at and self.access_expires_at > now + timedelta(seconds=SKEW_S):
            return self.access_token
        if self.refresh_token and self.refresh_expires_at and self.refresh_expires_at > now + timedelta(seconds=SKEW_S):
            if self._do_refresh():
                return self.access_token
        self._do_login()
        return self.access_token or ""

    def _lock(self):
        """Take a row lock, then re-read so we see any concurrent update."""
        self.ensure_one()
        self.env.cr.execute("SELECT id FROM custom_esb_session WHERE id = %s FOR UPDATE", (self.id,))
        self.invalidate_recordset(["access_token", "refresh_token", "access_expires_at", "refresh_expires_at"])

    def _invalidate_token(self):
        """Drop both cached tokens so the next call logs in afresh.

        Called when ESB rejects a token that has not expired by our clock —
        which is what an eviction by a competing login looks like. An eviction
        kills the whole ESB session, so the refresh token is dead too; keeping
        it would only buy a guaranteed-to-fail refresh round trip before the
        login we already know we need.
        """
        self.ensure_one()
        self.sudo().write(
            {
                "access_token": False,
                "access_expires_at": False,
                "refresh_token": False,
                "refresh_expires_at": False,
            }
        )

    def _adapter(self):
        """Core adapter used for the auth round trips themselves."""
        self.ensure_one()
        config = self.adapter_config_id
        if config.status == "disabled":
            raise UserError(_("ESB adapter '%s' is disabled.") % config.name)
        return config.get_adapter()

    def _do_login(self) -> bool:
        self.ensure_one()
        if not self.username or not self.credential_ref:
            self._fail(_("ESB session %s has no username/credential_ref configured.") % self.display_name)
            return False
        password = self._get_credential()
        if not password:
            self._fail(_("ir.config_parameter '%s' is empty — set the ESB password.") % self.credential_ref)
            return False
        resp = self._adapter().login(self.username, password)
        if not resp.ok:
            self._fail(resp.error or _("ESB login failed"))
            return False
        return self._store_tokens(resp, is_login=True)

    def _do_refresh(self) -> bool:
        self.ensure_one()
        resp = self._adapter().refresh(self.refresh_token)
        if not resp.ok:
            _logger.info("ESB %s: refresh rejected (%s), falling back to login", self.display_name, resp.error)
            return False
        return self._store_tokens(resp, is_login=False)

    def _store_tokens(self, resp, is_login: bool) -> bool:
        self.ensure_one()
        result = (resp.data or {}).get("result") or {}
        access = result.get("accessToken")
        if not access:
            self._fail(_("ESB auth response carried no accessToken"))
            return False
        now = fields.Datetime.now()
        vals = {
            "access_token": access,
            "access_expires_at": now + timedelta(seconds=ACCESS_TTL_S),
            "last_error": False,
        }
        if result.get("refreshToken"):
            vals["refresh_token"] = result["refreshToken"]
            vals["refresh_expires_at"] = now + timedelta(seconds=REFRESH_TTL_S)
        if is_login:
            vals.update(
                {
                    "last_login_at": now,
                    "login_count": (self.login_count or 0) + 1,
                    "company_code": result.get("companyCode") or False,
                    "company_id_esb": result.get("companyID") or 0,
                }
            )
        self.sudo().write(vals)
        return True

    def _fail(self, message: str):
        self.ensure_one()
        _logger.warning("ESB session %s: %s", self.display_name, message)
        self.sudo().write({"last_error": (message or "")[:255]})

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def action_test_connection(self):
        """Authenticate and read one branch page — the smoke test for new credentials."""
        self.ensure_one()
        token = self._ensure_token()
        if not token:
            raise UserError(_("Could not obtain an ESB token: %s") % (self.last_error or _("unknown error")))
        resp = self._adapter().get("branch")
        if not resp.ok:
            raise UserError(_("Authenticated, but GET /branch failed: %s") % resp.error)
        count = len(self._adapter()._rows(resp))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("ESB connection OK"),
                "message": _("%s branch(es) visible to this ESB user.") % count,
                "sticky": False,
            },
        }
