# -*- coding: utf-8 -*-
"""PPOB provider adapter abstraction (domain-verb registry).

Adapters are registered by name on ``custom.ppob.provider.adapter_class`` and
instantiated on demand by ``custom.ppob.provider._get_adapter()``. The protocol
is deliberately small -- ``inquiry``, ``pay``, ``status``, ``topup`` -- so
external teams (or the oracle-bridge module) can plug in their own
implementation without forking this module.

This is a SEPARATE registry from ``custom_adapter_framework`` on purpose: the
framework registry is config-instantiated (``cls(config)``) and verb-agnostic
(``call()``), while PPOB adapters are provider-instantiated and speak the four
business verbs. Concrete HTTP adapters still REUSE the framework for transport
credentials (via ``provider.adapter_config_id``) and observability (writing
``custom.adapter.call.log`` rows) -- see ``ppob_provider_adapter_http`` -- but
without the framework's shared retry loop, so a non-idempotent ``pay()`` is
never retried and cannot double-sell.

``status`` is REQUIRED on every adapter. The transaction reaper refuses to
auto-refund any transaction whose provider does not expose a status endpoint.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AdapterResult:
    ok: bool
    provider_ref: Optional[str] = None
    serial_token: Optional[str] = None
    amount: Optional[float] = None
    raw: Optional[dict] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


_ADAPTER_REGISTRY = {}


def register_adapter(name):
    """Decorator used by adapter subclasses to register by name."""

    def _wrap(cls):
        _ADAPTER_REGISTRY[name] = cls
        cls._adapter_name = name
        return cls

    return _wrap


def get_adapter_class(name):
    return _ADAPTER_REGISTRY.get(name)


def list_adapter_classes():
    return sorted(_ADAPTER_REGISTRY.keys())


class PPOBProviderAdapter:
    """Base adapter interface. Subclasses override the four methods."""

    def __init__(self, provider):
        self.provider = provider

    def inquiry(self, transaction) -> AdapterResult:
        raise NotImplementedError

    def pay(self, transaction) -> AdapterResult:
        raise NotImplementedError

    def status(self, provider_ref) -> AdapterResult:
        """Query provider for the final state of ``provider_ref``.

        MUST be implemented - the reaper refuses providers without it.
        """
        raise NotImplementedError

    def topup(self, amount) -> AdapterResult:
        """Top up the provider deposit from our operating bank."""
        raise NotImplementedError
