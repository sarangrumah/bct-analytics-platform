"""Cross-tenant access returns 403, with exactly the contract-02 body.

Two independent halves, and both are required:

* **The API answer.** A session for tenant A asking for tenant B gets HTTP 403 carrying exactly
  ``{"error": "tenant_scope_violation", "detail": "Session is not scoped to the requested tenant."}``
  -- and no hint about whether tenant B exists. The body is asserted character for character,
  because "some 403 with some message" is what a hand-rolled check produces and it is not what the
  frozen contract says.
* **The database answer**, which lives in ``test_05_tenant_isolation.py``. Master prompt §3.3 is
  explicit that application-level filtering alone is not sufficient, so an API that returns 403
  while the underlying query would have returned tenant B's rows is only half of the requirement.

The ``allowed_ou`` cases are here rather than in the isolation file because they are a *session*
property. Contract 02's GATE 3 amendment reversed the meaning of the empty list: ``[]`` now means
**no** operating units, and ``all_ou`` is the explicit, separate bypass. The old semantics would
have shown a user with no entitlement everything, so ``[]`` gets its own named test.
"""

from __future__ import annotations

import json

import pytest

from helpers import tokens, web

pytestmark = [pytest.mark.live]

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


def _query(token, filters=None, metric=None):
    payload = dict(BASE_QUERY)
    if metric:
        payload["metric"] = metric
    if filters:
        payload["filters"] = dict(payload["filters"], **filters)
    return web.request(
        web.semantic_url("/v1/query"), method="POST", payload=payload,
        headers={"Authorization": "Bearer %s" % token},
    )


def test_the_second_tenant_actually_holds_rows(semantic_up, marts_exist, evidence):
    """The precondition every cross-tenant assertion in this file and in test_05 depends on.

    `bct_t2` is **not** a second Odoo database -- only `bct` exists in Postgres. It is a
    warehouse-only fixture tenant, loaded by `make up-analytics`'s `load-fixture --tenant bct_t2`.
    That is a legitimate design, and it has one consequence worth its own test: if that fixture load
    is skipped or fails, every "tenant A cannot see tenant B" assertion passes by having nothing to
    leak. The 403 tests below and the RLS tests in test_05 are only evidence while this is green.
    """
    from helpers import db

    grid = db.grid(
        db.warehouse_admin(),
        "SELECT tenant_id, count(*) FROM marts.fct_sale_order_line GROUP BY 1 "
        "UNION ALL SELECT 'dim_partner:' || tenant_id, count(*) FROM marts.dim_partner GROUP BY 1 "
        "ORDER BY 1;",
    )
    evidence.add("rows per tenant, read past RLS as the superuser", grid)
    total = db.scalar(
        db.warehouse_admin(),
        "SELECT (SELECT count(*) FROM marts.fct_sale_order_line WHERE tenant_id = 'bct_t2') "
        "     + (SELECT count(*) FROM marts.dim_partner WHERE tenant_id = 'bct_t2');",
    )
    evidence.add("bct_t2 rows across two marts", total)
    assert int(total) > 0, (
        "tenant bct_t2 holds no rows in the marts, so every cross-tenant assertion in this suite "
        "would pass by having nothing to leak. Run `make up-analytics`, which loads the fixture "
        "tenant, before treating any isolation result as evidence."
    )


def test_tenant_a_requesting_tenant_b_gets_403_with_the_contract_body(semantic_up, evidence):
    token = tokens.valid(tokens.claims(tenant="bct"))
    response = _query(token, filters={"tenant_id": "bct_t2"})
    evidence.add(
        "tenant bct requesting tenant bct_t2",
        "HTTP %s\n%s" % (response.status, response.body),
    )
    assert response.status == 403, "expected 403, got %s: %s" % (response.status, response.body[:300])
    assert response.json() == CONTRACT_02_BODY, (
        "the 403 body does not match frozen contract 02 exactly.\n  got      %s\n  contract %s"
        % (json.dumps(response.json(), sort_keys=True), json.dumps(CONTRACT_02_BODY, sort_keys=True))
    )


def test_the_403_does_not_reveal_whether_the_other_tenant_exists(semantic_up, evidence):
    """The same answer for a real tenant and an invented one, byte for byte."""
    token = tokens.valid(tokens.claims(tenant="bct"))
    real = _query(token, filters={"tenant_id": "bct_t2"})
    invented = _query(token, filters={"tenant_id": "no_such_tenant_zz"})
    evidence.add(
        "existing tenant vs invented tenant",
        "existing  HTTP %s %s\ninvented  HTTP %s %s"
        % (real.status, real.body, invented.status, invented.body),
    )
    assert real.status == invented.status == 403
    assert real.body == invented.body, (
        "the response differs between an existing and a non-existent tenant, which leaks tenant "
        "existence to any authenticated user"
    )


def test_a_tenant_header_cannot_override_the_token(semantic_up, evidence):
    """Contract 02: `tenant_id` comes only from the verified token -- never a header or cookie."""
    token = tokens.valid(tokens.claims(tenant="bct"))
    response = web.request(
        web.semantic_url("/v1/query"), method="POST", payload=BASE_QUERY,
        headers={
            "Authorization": "Bearer %s" % token,
            "X-Tenant-Id": "bct_t2",
            "X-Tenant": "bct_t2",
        },
    )
    evidence.add("query with X-Tenant-Id: bct_t2 and a bct token", "HTTP %s\n%s"
                 % (response.status, response.body[:500]))
    assert response.status == 200, response.body[:300]
    assert response.json()["meta"]["tenant_id"] == "bct", (
        "a request header changed the tenant the query ran under"
    )


def test_the_rows_returned_belong_only_to_the_session_tenant(semantic_up, evidence):
    token = tokens.valid(tokens.claims(tenant="bct", all_ou=True))
    response = _query(token, filters=None)
    evidence.add("bct session, no tenant filter", "HTTP %s\n%s" % (response.status, response.body[:600]))
    assert response.status == 200, response.body[:300]
    body = response.json()
    assert body["meta"]["tenant_id"] == "bct"
    stray = [r for r in body["rows"] if r.get("tenant_id") not in (None, "bct")]
    assert not stray, "rows from another tenant came back: %r" % stray[:3]


def test_empty_allowed_ou_means_no_operating_units(semantic_up, evidence):
    """Contract 02's GATE 3 amendment, in the branch the old semantics got wrong.

    `allowed_ou: []` used to mean "all". It now means "only documents carrying no Operating Unit",
    matching `custom_operating_unit`'s record rules, which fail closed. A user with no entitlement
    must therefore see *less* than a user with `all_ou`, never more.
    """
    empty = tokens.valid(tokens.claims(tenant="bct", allowed_ou=[], all_ou=False))
    everything = tokens.valid(tokens.claims(tenant="bct", all_ou=True))

    restricted = _query(empty)
    unrestricted = _query(everything)
    evidence.add(
        "allowed_ou=[] vs all_ou=true",
        "allowed_ou=[]  HTTP %s rows=%s\nall_ou=true    HTTP %s rows=%s"
        % (restricted.status, len(restricted.json().get("rows", [])) if restricted.status == 200 else "-",
           unrestricted.status, len(unrestricted.json().get("rows", [])) if unrestricted.status == 200 else "-"),
    )
    assert restricted.status == 200, restricted.body[:300]
    assert unrestricted.status == 200, unrestricted.body[:300]
    restricted_rows = restricted.json()["rows"]
    unrestricted_rows = unrestricted.json()["rows"]
    assert len(restricted_rows) <= len(unrestricted_rows), (
        "allowed_ou=[] returned MORE rows (%d) than all_ou=true (%d). That is the exact privilege "
        "escalation the GATE 3 amendment to contract 02 was written to close."
        % (len(restricted_rows), len(unrestricted_rows))
    )
    assert unrestricted_rows, "the all_ou session saw nothing, so the comparison proves nothing"


def test_absent_all_ou_is_not_treated_as_a_bypass(semantic_up, evidence):
    """"Every verifier must treat absent `all_ou` as `false`. Never infer the bypass from emptiness.\""""
    payload = tokens.claims(tenant="bct", allowed_ou=[])
    payload.pop("all_ou", None)
    absent = tokens.valid(payload)
    everything = tokens.valid(tokens.claims(tenant="bct", all_ou=True))

    a = _query(absent)
    b = _query(everything)
    evidence.add(
        "all_ou claim ABSENT vs all_ou=true",
        "absent    HTTP %s rows=%s\nall_ou    HTTP %s rows=%s"
        % (a.status, len(a.json().get("rows", [])) if a.status == 200 else "-",
           b.status, len(b.json().get("rows", [])) if b.status == 200 else "-"),
    )
    assert a.status in (200, 401, 403), a.body[:300]
    if a.status == 200:
        assert len(a.json()["rows"]) <= len(b.json()["rows"]), (
            "a token with no all_ou claim saw as much as an explicit bypass; absence was read as true"
        )


def test_the_unassigned_ou_branch_is_actually_exercised(semantic_up, marts_exist, evidence):
    """`allowed_ou: []` maps to the UNASSIGNED member `-1`, not to `IS NULL`. Is that observable?

    Backend originally mapped `[]` to `operating_unit_id IS NULL` by analogy with Odoo's record
    rules, and corrected it to `= -1` because the marts carry an explicit UNASSIGNED member rather
    than a NULL. The fix is right. But on a mart where **no row carries `-1`** the old mapping and
    the new one both return zero rows, so the fix is indistinguishable from the bug it replaced.

    A fix that has never been observed to change an outcome is not yet known to work. So the metric
    is chosen from the data: whichever metric's source mart actually holds unassigned rows. Picking
    one up front is how this test first failed against `revenue_net`, whose mart legitimately has
    none -- a test asserting on the wrong subject, which is its own kind of false report.
    """
    import yaml

    from helpers import db, env

    catalogue = yaml.safe_load(
        (env.repo_root() / "analytics" / "semantic-api" / "metrics" / "core.yml").read_text(
            encoding="utf-8"
        )
    )
    metrics = catalogue if isinstance(catalogue, list) else catalogue.get("metrics", catalogue)

    report, usable = [], []
    for metric in metrics:
        mart = metric["source_model"]
        try:
            unassigned, total = db.query(
                db.warehouse_admin(),
                "SELECT count(*) FILTER (WHERE operating_unit_id = -1), count(*) "
                "FROM marts.%s;" % mart,
            )[0]
        except db.PsqlError as exc:
            report.append("%-28s %-24s (unreadable: %s)" % (metric["name"], mart, exc))
            continue
        report.append("%-28s %-24s unassigned=%-5s total=%s"
                      % (metric["name"], mart, unassigned, total))
        if int(unassigned) > 0:
            usable.append((metric["name"], mart, int(unassigned)))
    evidence.add("metric -> source mart -> rows carrying operating_unit_id = -1", "\n".join(report))

    if not usable:
        pytest.fail(
            "NOT COVERED: no mart backing any metric holds a row with operating_unit_id = -1, so "
            "`allowed_ou: []` -> `= -1` and the old `IS NULL` mapping are indistinguishable -- both "
            "return zero rows. Seed one document with no Operating Unit, rebuild with "
            "`make dbt-run`, and this test starts asserting instead of reporting."
        )

    name, mart, unassigned = usable[0]
    declared = next(m for m in metrics if m["name"] == name)

    # Ask for the metric on ITS OWN declared shape. Reusing the module-level query sent `date_day`
    # to a metric that does not declare it and got a 400 back -- a test failing on its own malformed
    # request while reporting it as a defect in the thing under test.
    payload = {
        "metric": name,
        "dimensions": ["operating_unit_id"],
        "filters": {
            key: (["2020-01-01", "2030-12-31"] if spec.get("type") == "daterange" else [])
            for key, spec in (declared.get("filters") or {}).items()
            if spec.get("required")
        },
        "limit": 50,
    }

    def ask(token):
        return web.request(
            web.semantic_url("/v1/query"), method="POST", payload=payload,
            headers={"Authorization": "Bearer %s" % token},
        )

    empty = tokens.valid(tokens.claims(tenant="bct", allowed_ou=[], all_ou=False))
    everything = tokens.valid(tokens.claims(tenant="bct", all_ou=True))

    restricted = ask(empty)
    unrestricted = ask(everything)
    evidence.add(
        "metric %r (mart %s, %d unassigned rows)" % (name, mart, unassigned),
        "allowed_ou=[]  HTTP %s  %s\nall_ou=true    HTTP %s  %s"
        % (restricted.status, restricted.body[:300],
           unrestricted.status, unrestricted.body[:300]),
    )
    assert restricted.status == 200, restricted.body[:300]
    assert unrestricted.status == 200, unrestricted.body[:300]

    restricted_rows = restricted.json()["rows"]
    unrestricted_rows = unrestricted.json()["rows"]
    assert restricted_rows, (
        "mart %s holds %d rows with operating_unit_id = -1, but an allowed_ou=[] session saw none. "
        "That is the `IS NULL` mapping, not the `= -1` one." % (mart, unassigned)
    )
    assert len(restricted_rows) < len(unrestricted_rows), (
        "the no-entitlement session saw as much as the explicit bypass (%d vs %d rows); the "
        "restriction is not restricting" % (len(restricted_rows), len(unrestricted_rows))
    )
    # And what it saw is ONLY the unassigned member -- the property that distinguishes the fix.
    seen = {r.get("operating_unit_id") for r in restricted_rows if "operating_unit_id" in r}
    if seen:
        assert seen == {-1}, (
            "an allowed_ou=[] session saw operating units %r; it must see only the UNASSIGNED "
            "member (-1)" % sorted(seen)
        )
