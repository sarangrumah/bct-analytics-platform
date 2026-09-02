# -*- coding: utf-8 -*-
"""Bank H2H adapter implementations.

Each Indonesian bank exposes a slightly different host-to-host API. These
adapters extend ``custom.adapter.base.BaseAdapter`` (HMAC signing, retry,
circuit breaker) and expose two business methods:

- ``inquiry_balance(account_number)``
- ``inquiry_statement(account_number, date_from, date_to)``

Only endpoint *paths* are bank-specific; the transport (signing, retries,
breaker) is inherited. Endpoint URLs marked with ``# TODO(endpoint)`` are
left as placeholders because each bank requires onboarding (test-bed URL
issued at contract signing) — production deployments fill these in via
the adapter config's ``base_url`` plus the per-bank path constants below.
"""

from __future__ import annotations

import logging
from datetime import date

from odoo.addons.custom_adapter_framework.models.adapter_base import (
    AdapterResponse,
    BaseAdapter,
)
from odoo.addons.custom_adapter_framework.models.adapter_registry import (
    register_adapter,
)

_logger = logging.getLogger(__name__)


def _iso(d: date | None) -> str:
    if d is None:
        return ""
    if isinstance(d, str):
        return d
    return d.isoformat()


@register_adapter("bank_bca_h2h")
class BcaH2HAdapter(BaseAdapter):
    """BCA Business API H2H adapter.

    Reference: developer.bca.co.id. Auth = HMAC-SHA256 over
    ``METHOD:Path:AccessToken:LowerCase(SHA256(Body)):Timestamp``. The
    framework's HMAC signer covers the simpler ``ts || body`` shape; for
    BCA's stricter canonical form, downstream production code should
    override ``_sign_request`` — left as an inherit point.
    """

    PATH_BALANCE = "/banking/v3/corporates/accounts/{acct}"
    PATH_STATEMENT = "/banking/v3/corporates/accounts/{acct}/statements"

    def inquiry_balance(self, account_number: str) -> AdapterResponse:
        endpoint = self.PATH_BALANCE.format(acct=account_number)
        return self.call(endpoint, method="GET")

    def inquiry_statement(self, account_number: str, date_from: date, date_to: date) -> AdapterResponse:
        endpoint = (
            self.PATH_STATEMENT.format(acct=account_number) + f"?EndDate={_iso(date_to)}&StartDate={_iso(date_from)}"
        )
        resp = self.call(endpoint, method="GET")
        if resp.ok and resp.data:
            resp.data = {"lines": self._normalize_lines(resp.data)}
        return resp

    @staticmethod
    def _normalize_lines(payload: dict) -> list[dict]:
        """BCA returns ``Data: [{TransactionDate, TrailerCode, Amount,
        TransactionType, ...}]``; map to our internal shape."""
        out = []
        for row in payload.get("Data", payload.get("data", [])) or []:
            amt = float(row.get("Amount") or 0.0)
            if (row.get("TransactionType") or "").upper() in ("D", "DEBIT"):
                amt = -abs(amt)
            else:
                amt = abs(amt)
            out.append(
                {
                    "date": row.get("TransactionDate") or row.get("Date"),
                    "description": row.get("TransactionName") or row.get("Description") or "",
                    "ref": row.get("TrailerCode") or row.get("Reference") or "",
                    "amount": amt,
                }
            )
        return out


@register_adapter("bank_generic_h2h")
class GenericBankH2HAdapter(BaseAdapter):
    """Generic HTTP H2H placeholder.

    Configure ``base_url`` on the adapter config and the bank's expected
    paths via ``ir.config_parameter`` keys ``custom_bank_import.<code>.path_balance``
    and ``.path_statement`` (default ``/balance`` and ``/statement``).
    """

    def _path(self, suffix: str, default: str) -> str:
        key = f"custom_bank_import.{getattr(self.config, 'name', 'generic')}.path_{suffix}"
        return self.env["ir.config_parameter"].sudo().get_param(key, default) or default

    def inquiry_balance(self, account_number: str) -> AdapterResponse:
        return self.call(
            self._path("balance", "/balance"),
            method="POST",
            payload={"account_number": account_number},
        )

    def inquiry_statement(self, account_number: str, date_from: date, date_to: date) -> AdapterResponse:
        resp = self.call(
            self._path("statement", "/statement"),
            method="POST",
            payload={
                "account_number": account_number,
                "date_from": _iso(date_from),
                "date_to": _iso(date_to),
            },
        )
        if resp.ok and resp.data and "lines" not in resp.data:
            # Pass through if upstream already conforms; else assume the
            # body itself is the list.
            data = resp.data
            if isinstance(data, list):
                resp.data = {"lines": data}
        return resp


# Aliases for other Indonesian banks. Each bank's onboarding will plug a
# proper subclass with bank-specific signing/canonicalisation; until then
# they share the generic transport but get distinct adapter_type names so
# circuit breakers and logs are tracked separately.


@register_adapter("bank_mandiri_h2h")
class MandiriH2HAdapter(GenericBankH2HAdapter):
    pass


@register_adapter("bank_bni_h2h")
class BniH2HAdapter(GenericBankH2HAdapter):
    pass


@register_adapter("bank_bri_h2h")
class BriH2HAdapter(GenericBankH2HAdapter):
    pass


@register_adapter("bank_cimb_h2h")
class CimbH2HAdapter(GenericBankH2HAdapter):
    pass


@register_adapter("bank_permata_h2h")
class PermataH2HAdapter(GenericBankH2HAdapter):
    pass


@register_adapter("bank_danamon_h2h")
class DanamonH2HAdapter(GenericBankH2HAdapter):
    pass
