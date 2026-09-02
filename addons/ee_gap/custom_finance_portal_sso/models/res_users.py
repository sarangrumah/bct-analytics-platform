# -*- coding: utf-8 -*-
"""Map Keycloak roles → Finance Portal groups on OAuth/OIDC sign-in."""

from __future__ import annotations

import json
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

ROLE_MAP_PARAM = "custom_finance_portal_sso.role_group_map"

# Keycloak role/group name → Finance Portal group xmlid. Override per tenant via
# the ROLE_MAP_PARAM ir.config_parameter (JSON).
DEFAULT_ROLE_MAP = {
    "finance_manager": "custom_finance_portal.group_finance_manager",
    "finance_officer": "custom_finance_portal.group_finance_officer",
    "finance_tax": "custom_finance_portal.group_finance_tax",
    "finance_requester": "custom_finance_portal.group_finance_user",
    "finance_vendor": "custom_finance_portal.group_finance_vendor",
}


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _auth_oauth_signin(self, provider, validation, params):
        login = super()._auth_oauth_signin(provider, validation, params)
        try:
            user = self.sudo().search([("login", "=", login)], limit=1)
            if user:
                user._finance_sso_apply_roles(validation or {})
        except Exception as e:  # never block login on a mapping hiccup
            _logger.warning("Finance SSO role mapping failed for %s: %s", login, e)
        return login

    # ------------------------------------------------------------------
    def _finance_sso_role_map(self) -> dict:
        raw = self.env["ir.config_parameter"].sudo().get_param(ROLE_MAP_PARAM)
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, TypeError):
                _logger.warning("Invalid %s — using defaults", ROLE_MAP_PARAM)
        return DEFAULT_ROLE_MAP

    @staticmethod
    def _finance_sso_extract_roles(validation: dict) -> set:
        """Collect roles from common Keycloak claim shapes."""
        roles: list = []
        if not isinstance(validation, dict):
            return set()
        roles += list((validation.get("realm_access") or {}).get("roles") or [])
        roles += list(validation.get("groups") or [])
        roles += list(validation.get("roles") or [])
        for _client, acc in (validation.get("resource_access") or {}).items():
            if isinstance(acc, dict):
                roles += list(acc.get("roles") or [])
        # Keycloak group paths arrive as "/finance_vendor" — strip the slash.
        return {r.lstrip("/") for r in roles if r}

    def _finance_sso_apply_roles(self, validation: dict):
        self.ensure_one()
        mapping = self._finance_sso_role_map()
        roles = self._finance_sso_extract_roles(validation)
        group_ids = []
        for role in roles:
            xmlid = mapping.get(role)
            if not xmlid:
                continue
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group:
                group_ids.append(group.id)
        if group_ids:
            self.sudo().write({"group_ids": [(4, gid) for gid in group_ids]})
            _logger.info("Finance SSO: granted %s groups %s", self.login, group_ids)
        return True
