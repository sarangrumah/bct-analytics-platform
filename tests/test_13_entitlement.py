"""Contract 07 — the entitlement gate at the semantic API.

Every assertion here is about a door being CLOSED. That bias is deliberate: the read path
(`tenant_registry.is_active()` -> gateway -> claims) has worked since GATE 3 and was never the
problem. What was missing is that `products` was minted into every token and read by nobody, so a
tenant on the `odoo_care` plan passed every gate and used a dashboard their plan does not grant.
A test that only proves an entitled tenant gets 200 would have passed throughout that entire period.

Tokens are minted here rather than obtained from the gateway on purpose. The gate under test reads
the verified token and nothing else, so minting lets each case vary exactly one claim. The
end-to-end path — control plane row -> gateway lookup -> claim -> refusal — is evidence collected
by the Lead against the running stack, not something this file can isolate.

402 AND NOT 403 is the point of `test_scope_violation_still_wins`. Contract 02 owns 403 for
cross-tenant violations; if the entitlement check were placed first, an unentitled session probing
another tenant would learn from the 402 that it had reached the entitlement layer at all.
"""

from __future__ import annotations

import pytest

from helpers import tokens, web

pytestmark = [pytest.mark.live]

SUBSCRIPTION_INACTIVE_BODY = {
    "error": "subscription_inactive",
    "detail": "This tenant's subscription is not active.",
}
PRODUCT_NOT_ENTITLED_BODY = {
    "error": "product_not_entitled",
    "detail": "This tenant's plan does not include ATHERA Insight.",
}
CONTRACT_02_BODY = {
    "error": "tenant_scope_violation",
    "detail": "Session is not scoped to the requested tenant.",
}

BASE_QUERY = {
    "metric": "revenue_net",
    "dimensions": ["date_day"],
    "filters": {"date_range": ["2026-01-01", "2026-12-31"]},
    "limit": 5,
}


def _query(token, filters=None):
    payload = dict(BASE_QUERY)
    if filters:
        payload["filters"] = dict(payload["filters"], **filters)
    return web.request(
        web.semantic_url("/v1/query"), method="POST", payload=payload,
        headers={"Authorization": "Bearer %s" % token},
    )


def test_entitled_tenant_is_served(semantic_up, evidence):
    """The baseline. Without it, every refusal below could be a service that refuses everything."""
    token = tokens.valid(tokens.claims(tenant="bct"))
    response = _query(token)
    evidence.add("entitled session", "%s %s" % (response.status, response.body[:200]))
    assert response.status == 200, response.body


def test_inactive_subscription_is_refused_with_402(semantic_up, evidence):
    token = tokens.valid(tokens.claims(tenant="bct", subscription_active=False))
    response = _query(token)
    evidence.add("subscription_active=false", "%s %s" % (response.status, response.body))
    assert response.status == 402, response.body
    assert response.json() == SUBSCRIPTION_INACTIVE_BODY


def test_plan_without_insight_is_refused_with_402(semantic_up, evidence):
    """The `odoo_care` case: paying, active, and not entitled to this product."""
    token = tokens.valid(tokens.claims(tenant="bct", products=["odoo"]))
    response = _query(token)
    evidence.add("products=['odoo']", "%s %s" % (response.status, response.body))
    assert response.status == 402, response.body
    assert response.json() == PRODUCT_NOT_ENTITLED_BODY


def test_absent_claims_are_refused_not_trusted(semantic_up, evidence):
    """A token predating contract 07 grants nothing.

    Same rule `all_ou` follows. If absence read as "entitled", the migration window itself would
    have been the bypass, and it would have closed silently once every token carried the claim.
    """
    payload = tokens.claims(tenant="bct")
    payload.pop("subscription_active")
    payload.pop("products")
    response = _query(tokens.valid(payload))
    evidence.add("claims absent", "%s %s" % (response.status, response.body))
    assert response.status == 402, response.body


def test_empty_products_is_none_not_all(semantic_up, evidence):
    token = tokens.valid(tokens.claims(tenant="bct", products=[]))
    response = _query(token)
    evidence.add("products=[]", "%s %s" % (response.status, response.body))
    assert response.status == 402, response.body
    assert response.json() == PRODUCT_NOT_ENTITLED_BODY


def test_scope_violation_still_wins(semantic_up, evidence):
    """An unentitled session probing another tenant gets 403, not 402.

    The ordering is the assertion. Reversing the two checks in `main.py` turns this red.
    """
    token = tokens.valid(tokens.claims(tenant="bct", subscription_active=False))
    response = _query(token, filters={"tenant_id": "bct_t2"})
    evidence.add("unentitled + cross-tenant", "%s %s" % (response.status, response.body))
    assert response.status == 403, response.body
    assert response.json() == CONTRACT_02_BODY
