import assert from "node:assert/strict";
import { test } from "node:test";

import { toSession } from "../src/lib/claims.ts";

/**
 * The GATE 3 amendment to contract 02, exercised directly.
 *
 * How these were made to go red: `all_ou: payload.all_ou === true` was temporarily changed to
 * `all_ou: payload.all_ou !== false` - the "obvious" reading, and the one that reintroduces the
 * privilege escalation. Both "absent" tests failed and the two positive tests kept passing, which
 * is what makes the pair worth having: a test that only checks the true case cannot tell the two
 * implementations apart.
 */

const base = {
  iss: "https://login-gateway.local/",
  aud: "insight-portal",
  sub: "odoo:bct:2",
  tenant_id: "bct",
  odoo_uid: 2,
  roles: ["analytics.viewer"],
  company_ids: [1],
  iat: 1788138826,
  exp: 1788142426,
};

test("an absent all_ou claim grants no bypass", () => {
  const session = toSession({ ...base, allowed_ou: [] });
  assert.notEqual(session, null);
  assert.equal(session?.all_ou, false, "absent all_ou must be false, never inferred as a bypass");
});

test("an absent all_ou claim alongside a populated allowed_ou still grants no bypass", () => {
  const session = toSession({ ...base, allowed_ou: [1, 4, 9] });
  assert.equal(session?.all_ou, false);
  assert.deepEqual(session?.allowed_ou, [1, 4, 9]);
});

test("an empty allowed_ou stays empty and is never widened to all", () => {
  const session = toSession({ ...base, allowed_ou: [], all_ou: false });
  assert.deepEqual(session?.allowed_ou, [], "empty entitlement means NO operating units");
  assert.equal(session?.all_ou, false);
});

test("all_ou true is honoured only when it is literally true", () => {
  assert.equal(toSession({ ...base, allowed_ou: [], all_ou: true })?.all_ou, true);
  for (const truthy of ["true", 1, "yes", {}] as unknown[]) {
    const session = toSession({ ...base, allowed_ou: [], all_ou: truthy } as never);
    assert.equal(session?.all_ou, false, "only the boolean true is the bypass, not a truthy value");
  }
});

test("a payload with no tenant_id produces no session at all", () => {
  assert.equal(toSession({ ...base, tenant_id: undefined, allowed_ou: [] } as never), null);
  assert.equal(toSession({ ...base, tenant_id: "", allowed_ou: [] } as never), null);
});

test("non-numeric operating unit ids are dropped rather than coerced", () => {
  const session = toSession({ ...base, allowed_ou: [1, "2", null, 3] } as never);
  assert.deepEqual(session?.allowed_ou, [1, 3]);
});
