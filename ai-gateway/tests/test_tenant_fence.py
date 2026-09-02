"""The tenant fence — the test the whole ATHERA Agent claim rests on.

"The agent only knows your data" is a product promise, and a prompt cannot keep
it. `_validate_plan` is the last thing between a model's answer and Odoo's ORM,
and it is the only one of the four structural properties in
`ai-gateway/app/main.py` that can be exercised without a model, a database or a
network. So it is exercised here, hard, in BOTH directions.

The negative cases are the point. A fence that has never been observed to
refuse anything is not yet known to be a fence: a `_validate_plan` that
returned `None` unconditionally would pass a positive-only suite and let every
hallucinated model straight through.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Importing app.main runs create_app()? No -- create_app is a factory and is not
# called at import, so this needs no secret and no provider.
from app.main import _validate_plan  # noqa: E402

HINT = [
    {"model": "account.move", "fields": ["name", "amount_total", "state", "partner_id"]},
    {"model": "sale.order", "fields": ["name", "date_order", "amount_total"]},
]


def test_a_plan_inside_the_fence_is_allowed():
    """The positive control. Without it the negatives below prove only that
    the function rejects things, not that it discriminates."""
    plan = {
        "model": "account.move",
        "fields": ["name", "amount_total"],
        "domain": [["state", "=", "posted"], ["amount_total", ">", 100000000]],
    }
    assert _validate_plan(plan, HINT) is None


def test_a_second_offered_model_is_also_allowed():
    plan = {"model": "sale.order", "fields": ["name", "date_order"], "domain": []}
    assert _validate_plan(plan, HINT) is None


def test_a_model_that_was_not_offered_is_refused():
    """The one that matters. res.users is a real Odoo model holding password
    hashes and it is NOT in the hint, so naming it must be refused whatever the
    model was persuaded to produce."""
    plan = {"model": "res.users", "fields": ["login"], "domain": []}
    fault = _validate_plan(plan, HINT)
    assert fault is not None
    assert "res.users" in fault


def test_a_field_that_was_not_offered_is_refused():
    plan = {"model": "account.move", "fields": ["name", "password"], "domain": []}
    fault = _validate_plan(plan, HINT)
    assert fault is not None
    assert "password" in fault


def test_a_domain_filtering_on_an_unoffered_field_is_refused():
    """Selecting a hidden field and FILTERING on one are different leaks, and
    the second is the quieter of the two: the value never appears in the
    output, but the row set it produces still discloses it."""
    plan = {
        "model": "account.move",
        "fields": ["name"],
        "domain": [["create_uid", "=", 1]],
    }
    fault = _validate_plan(plan, HINT)
    assert fault is not None
    assert "create_uid" in fault


def test_fields_from_the_wrong_offered_model_are_refused():
    """Both models are in the hint; the field belongs to the other one. A fence
    that merged every offered field into one set would pass this by accident."""
    plan = {"model": "sale.order", "fields": ["name", "state"], "domain": []}
    fault = _validate_plan(plan, HINT)
    assert fault is not None
    assert "state" in fault


def test_an_empty_plan_is_a_refusal_to_guess_and_is_allowed():
    """The prompt tells the model to return an empty plan rather than invent a
    model it was not shown. That has to be permitted, or the safe answer is the
    one thing the fence rejects."""
    assert _validate_plan({"model": None, "fields": [], "domain": []}, HINT) is None


def test_an_empty_hint_fences_everything_out():
    """Defence in depth. main.py refuses an empty schema_hint before the model
    is called at all; if that check were ever removed, this makes the fence
    fail closed rather than open."""
    plan = {"model": "account.move", "fields": ["name"], "domain": []}
    assert _validate_plan(plan, []) is not None


@pytest.mark.parametrize("malformed", [
    {"model": "account.move", "fields": None, "domain": None},
    {"model": "account.move"},
])
def test_a_malformed_plan_does_not_crash_the_fence(malformed):
    """A model that returns something unexpected must not take the fence down
    with it -- an exception here would become a 500, and a 500 is not a refusal.
    """
    _validate_plan(malformed, HINT)
