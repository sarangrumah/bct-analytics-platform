# -*- coding: utf-8 -*-
"""Outbound adapters Odoo -> external WMS / host.

Both classes subclass ``BaseAdapter`` from ``custom_adapter_framework`` and add
nothing but verb sugar and an endpoint map: the HMAC signing, the exponential
backoff retry, the closed/open/half-open circuit breaker and the append-only
``custom.adapter.call.log`` write all come from the framework. Nothing here
should ever call ``requests`` directly or implement its own retry loop.

Registration happens at import time (see ``models/__init__.py``, which imports
this file first) so ``custom.adapter.config.adapter_type`` lists both.
"""

from __future__ import annotations

from odoo.addons.custom_adapter_framework.models.adapter_base import AdapterResponse, BaseAdapter
from odoo.addons.custom_adapter_framework.models.adapter_registry import register_adapter

#: Adapter type strings, as stored in ``custom.adapter.config.adapter_type``.
WMS_HOST = "wms_host"
WMS_SAP_HOST = "wms_sap_host"

#: The canonical outbound event verbs. Kept in sync with
#: ``wms.integration.event.event_type`` — the outbox refuses to enqueue an
#: event_type that has no endpoint here.
EVENT_TYPES = [
    ("goods_receipt", "Goods Receipt Confirmed"),
    ("putaway_done", "Putaway Done"),
    ("pick_confirmed", "Pick Confirmed"),
    ("pack_created", "Pack / Package Created"),
    ("goods_issue", "Goods Issue (Delivery Validated)"),
    ("stock_adjustment", "Stock Adjustment (Cycle-Count Variance)"),
]


@register_adapter(WMS_HOST)
class WmsHostAdapter(BaseAdapter):
    """Generic REST WMS host."""

    #: event_type -> endpoint path appended to ``config.base_url``.
    ENDPOINTS = {
        "goods_receipt": "wms/goods-receipt",
        "putaway_done": "wms/putaway",
        "pick_confirmed": "wms/pick",
        "pack_created": "wms/pack",
        "goods_issue": "wms/goods-issue",
        "stock_adjustment": "wms/stock-adjustment",
    }

    # ------------------------------------------------------------------
    # Generic dispatch
    # ------------------------------------------------------------------

    def endpoint_for(self, event_type: str) -> str | None:
        return self.ENDPOINTS.get(event_type)

    def push_event(self, event_type: str, payload: dict) -> AdapterResponse:
        """Push one outbox event. Returns an ``AdapterResponse``; never raises
        for transport errors (the framework converts those into ``ok=False``).

        May raise ``CircuitBreakerOpenError`` when the breaker is open — the
        outbox catches that and leaves the row pending.
        """
        endpoint = self.endpoint_for(event_type)
        if not endpoint:
            return AdapterResponse(ok=False, status_code=0, error=f"unmapped_event_type:{event_type}")
        return self.call(endpoint, payload=payload, method="POST")

    # ------------------------------------------------------------------
    # Verb sugar — one per business event
    # ------------------------------------------------------------------

    def push_goods_receipt(self, payload: dict) -> AdapterResponse:
        return self.push_event("goods_receipt", payload)

    def push_putaway_done(self, payload: dict) -> AdapterResponse:
        return self.push_event("putaway_done", payload)

    def push_pick_confirmed(self, payload: dict) -> AdapterResponse:
        return self.push_event("pick_confirmed", payload)

    def push_pack_created(self, payload: dict) -> AdapterResponse:
        return self.push_event("pack_created", payload)

    def push_goods_issue(self, payload: dict) -> AdapterResponse:
        return self.push_event("goods_issue", payload)

    def push_stock_adjustment(self, payload: dict) -> AdapterResponse:
        return self.push_event("stock_adjustment", payload)


@register_adapter(WMS_SAP_HOST)
class WmsSapHostAdapter(WmsHostAdapter):
    """SAP-flavoured WMS host.

    Same protocol, different paths: an SAP landscape is normally fronted by a
    PI/CPI or a custom OData/REST facade that maps these to the WMMBID / DELVRY
    IDoc families. Only the path map differs, so the SAP edge inherits every
    retry / breaker / logging behaviour of the generic adapter.
    """

    ENDPOINTS = {
        "goods_receipt": "sap/wms/goods-receipt",
        "putaway_done": "sap/wms/transfer-order-confirm",
        "pick_confirmed": "sap/wms/pick-confirm",
        "pack_created": "sap/wms/handling-unit",
        "goods_issue": "sap/wms/goods-issue",
        "stock_adjustment": "sap/wms/physical-inventory",
    }
