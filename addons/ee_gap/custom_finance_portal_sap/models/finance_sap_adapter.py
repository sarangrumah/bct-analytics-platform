# -*- coding: utf-8 -*-
"""Bridge adapters for the SAP and HRIS edges.

Both subclass ``BaseAdapter`` from ``custom_adapter_framework`` — they inherit
the HMAC signing, retry, circuit breaker and call-log behaviour and only add
convenience verbs. Registration via ``@register_adapter`` happens at import so
the ``custom.adapter.config.adapter_type`` selection lists them.
"""

from __future__ import annotations

from odoo.addons.custom_adapter_framework.models.adapter_base import BaseAdapter
from odoo.addons.custom_adapter_framework.models.adapter_registry import register_adapter

SAP_BRIDGE = "finance_sap_bridge"
HRIS_BRIDGE = "finance_hris_bridge"


@register_adapter(SAP_BRIDGE)
class FinanceSapBridgeAdapter(BaseAdapter):
    """Odoo -> SAP bridge (Kafka producer + request/reply on the bridge side)."""

    def push_document(self, payload: dict):
        """Push an approved Finance Portal document to SAP (GL / MIRO)."""
        return self.call("finance/push", payload=payload, method="POST")

    def get_master(self, kind: str, since: str | None = None):
        """Pull a master-data feed (supplier, coa, budget, item_category, ...)."""
        return self.call("finance/master/%s" % kind, payload={"since": since}, method="POST")

    def lookup_pr(self, pr_number: str):
        """Realtime PR lookup (value + status) used by the > Rp 1jt rule."""
        return self.call("finance/pr/lookup", payload={"pr_number": pr_number}, method="POST")

    def lookup_po_gr(self, po_number: str):
        """Realtime PO + GR lookup for vendor invoices."""
        return self.call("finance/po-gr/lookup", payload={"po_number": po_number}, method="POST")


@register_adapter(HRIS_BRIDGE)
class FinanceHrisBridgeAdapter(BaseAdapter):
    """Odoo <- HRIS bridge (employee master + travel mirror)."""

    def get_master(self, kind: str, since: str | None = None):
        return self.call("hris/master/%s" % kind, payload={"since": since}, method="POST")

    def get_travel(self, since: str | None = None):
        return self.call("hris/travel", payload={"since": since}, method="POST")
