"""The policy seam: what the loader refuses to do, and why refusing is the feature."""

from __future__ import annotations

import pytest

from bct_cdc.policy import (
    ColumnPolicy,
    Policy,
    PolicyInconsistent,
    UnclassifiedColumn,
    UnhashableColumn,
    publication_column_list,
)

SALT = "bct-demo-salt"


def row(table, column, pdp_class, transform, mask_null=False):
    return ColumnPolicy(table, column, pdp_class, transform, mask_null)


def simple_policy():
    return Policy([
        row("res_partner", "id", "internal", "none"),
        row("res_partner", "name", "personal", "hmac_sha256"),
        row("res_partner", "comment", "sensitive", "hmac_sha256_nullable", mask_null=True),
        row("res_partner", "vat", "sensitive", "hmac_sha256_nullable", mask_null=False),
        row("res_partner", "active", "public", "none"),
        row("res_users", "id", "internal", "none"),
        row("res_users", "password", "secret", "drop"),
        row("res_users", "login", "personal", "hmac_sha256"),
    ])


TYPES = {
    "id": "integer",
    "name": "character varying",
    "comment": "text",
    "vat": "character varying",
    "active": "boolean",
    "login": "character varying",
    "password": "character varying",
}


def test_secret_columns_are_absent_from_the_plan_entirely():
    plan = simple_policy().plan("res_users", ["id", "login", "password"], SALT, TYPES)
    # Not present with a 'drop' action -- absent. The loader must not be able to name it.
    assert "password" not in plan.columns
    assert "password" not in plan.select_columns


def test_unclassified_column_is_a_hard_failure():
    with pytest.raises(UnclassifiedColumn) as exc:
        simple_policy().plan("res_partner", ["id", "name", "surprise_column"], SALT, TYPES)
    assert "surprise_column" in str(exc.value)
    # It must never quietly become public.
    assert "public" not in str(exc.value).split("default")[0].lower() or "never defaulted" in str(exc.value)


def test_unknown_table_is_a_hard_failure():
    with pytest.raises(UnclassifiedColumn):
        simple_policy().plan("some_new_table", ["id"], SALT, TYPES)


def test_hmac_on_a_non_text_column_is_a_hard_failure():
    """The Lead's guard: classified, but unhashable.

    res.partner.barcode is jsonb and company_dependent. Hashing it yields a key that changes when
    any single company's value changes and leaks how many companies hold one -- while looking
    exactly like a working hash. The unclassified check cannot catch it, because it *is* classified.
    """
    policy = Policy([
        row("res_partner", "id", "internal", "none"),
        row("res_partner", "barcode", "personal", "hmac_sha256"),
    ])
    types = {"id": "integer", "barcode": "jsonb"}
    with pytest.raises(UnhashableColumn) as exc:
        policy.plan("res_partner", ["id", "barcode"], SALT, types)
    assert "barcode (jsonb)" in str(exc.value)


def test_the_same_column_as_sensitive_mask_null_is_accepted():
    policy = Policy([
        row("res_partner", "id", "internal", "none"),
        row("res_partner", "barcode", "sensitive", "hmac_sha256_nullable", mask_null=True),
    ])
    plan = policy.plan("res_partner", ["id", "barcode"], SALT, {"id": "integer", "barcode": "jsonb"})
    assert plan.columns["barcode"] == "null"


def test_contract_forbidden_class_transform_pair_is_rejected():
    with pytest.raises(PolicyInconsistent):
        Policy([row("res_partner", "name", "personal", "none")])


def test_apply_masks_personal_and_nulls_free_text():
    plan = simple_policy().plan(
        "res_partner", ["id", "name", "comment", "vat", "active"], SALT, TYPES
    )
    out = plan.apply({
        "id": 7, "name": "Budi Santoso", "comment": "a private note",
        "vat": "01.234.567.8-901.000", "active": True,
    })
    assert out["id"] == 7
    assert out["active"] is True
    assert out["name"] == "a5c30f115ac845dd0cfafabe0326c71de7f1e7d3a869d252c4caa894ab4b978b"
    assert out["comment"] is None            # sensitive free text -> NULL, never a digest
    assert len(out["vat"]) == 64             # sensitive non-free-text -> digest


def test_apply_preserves_null_rather_than_hashing_it():
    plan = simple_policy().plan("res_partner", ["id", "name"], SALT, TYPES)
    assert plan.apply({"id": 1, "name": None})["name"] is None


def test_apply_refuses_a_column_not_in_the_plan():
    plan = simple_policy().plan("res_users", ["id", "login"], SALT, TYPES)
    with pytest.raises(UnclassifiedColumn):
        plan.apply({"id": 1, "login": "x", "password": "leaked"})


def test_publication_column_list_excludes_secrets_and_keeps_the_key():
    plan = simple_policy().plan("res_users", ["id", "login", "password"], SALT, TYPES)
    columns = publication_column_list(plan)
    assert "password" not in columns
    assert "id" in columns


def test_publication_column_list_requires_the_replica_identity_column():
    policy = Policy([row("res_partner", "name", "personal", "hmac_sha256")])
    plan = policy.plan("res_partner", ["name"], SALT, {"name": "text"})
    with pytest.raises(UnclassifiedColumn):
        publication_column_list(plan)
