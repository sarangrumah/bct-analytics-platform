# -*- coding: utf-8 -*-
"""Deterministic mock adapter used for smoke tests and development.

Behaviour is controlled by fields on ``custom.ppob.provider``:

* ``mock_outcome`` - success / fail / timeout, default success.
"""

import secrets

from .ppob_provider_adapter_base import (
    AdapterResult,
    PPOBProviderAdapter,
    register_adapter,
)


@register_adapter("ppob_mock")
class MockProviderAdapter(PPOBProviderAdapter):
    def _gen_ref(self):
        return "MOCK-" + secrets.token_hex(6).upper()

    def inquiry(self, transaction):
        return AdapterResult(
            ok=True,
            provider_ref=self._gen_ref(),
            amount=transaction.sell_price,
            raw={"mock": True, "stage": "inquiry"},
        )

    def pay(self, transaction):
        outcome = (self.provider.mock_outcome or "success").lower()
        if outcome == "success":
            return AdapterResult(
                ok=True,
                provider_ref=self._gen_ref(),
                serial_token="SN-" + secrets.token_hex(8).upper(),
                amount=transaction.cost_price,
                raw={"mock": True, "stage": "pay", "outcome": "success"},
            )
        if outcome == "fail":
            return AdapterResult(
                ok=False,
                error_code="MOCK_FAIL",
                error_message="Mock adapter configured to fail.",
                raw={"mock": True, "stage": "pay", "outcome": "fail"},
            )
        return AdapterResult(
            ok=False,
            error_code="TIMEOUT",
            error_message="Mock adapter configured to time out.",
            raw={"mock": True, "stage": "pay", "outcome": "timeout"},
        )

    def status(self, provider_ref):
        return AdapterResult(
            ok=True,
            provider_ref=provider_ref,
            raw={"mock": True, "stage": "status", "state": "success"},
        )

    def topup(self, amount):
        return AdapterResult(
            ok=True,
            provider_ref=self._gen_ref(),
            amount=amount,
            raw={"mock": True, "stage": "topup"},
        )
