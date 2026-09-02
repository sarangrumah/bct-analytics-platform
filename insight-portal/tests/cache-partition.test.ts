import assert from "node:assert/strict";
import { test } from "node:test";

import { cacheClear, cacheGet, cacheKey, cacheSet } from "../src/lib/cache.ts";

/**
 * The server-side aggregate cache must be partitioned by the verified session.
 *
 * This is not a theoretical concern. `/v1/query` request bodies carry no tenant - the tenant comes
 * from the token - so a cache keyed on the body alone would return one tenant's rows to another
 * tenant's request, and every layer below would be innocent.
 *
 * How this was made to go red: the `identity.tenant_id` segment was removed from the key template
 * in `src/lib/cache.ts`. "two sessions in different tenants never share a cache entry" failed
 * immediately. Removing the `all_ou` segment instead failed the entitlement test and left the
 * tenant test green, which is why they are separate assertions rather than one.
 */

const body = JSON.stringify({ metric: "revenue_net", dimensions: ["date_month"] });

test("two sessions in different tenants never share a cache entry", () => {
  const a = cacheKey({ sub: "odoo:bct:2", tenant_id: "bct", all_ou: true, allowed_ou: [] }, body);
  const b = cacheKey(
    { sub: "odoo:bct:2", tenant_id: "bct_t2", all_ou: true, allowed_ou: [] },
    body,
  );
  assert.notEqual(a, b, "the key must change when only the tenant changes");
});

test("the operating unit bypass changes the cache key", () => {
  const scoped = cacheKey(
    { sub: "odoo:bct:5", tenant_id: "bct", all_ou: false, allowed_ou: [] },
    body,
  );
  const bypass = cacheKey(
    { sub: "odoo:bct:5", tenant_id: "bct", all_ou: true, allowed_ou: [] },
    body,
  );
  assert.notEqual(scoped, bypass, "all_ou changes what the query returns, so it must change the key");
});

test("a different operating unit entitlement changes the cache key", () => {
  const one = cacheKey({ sub: "s", tenant_id: "bct", all_ou: false, allowed_ou: [1] }, body);
  const two = cacheKey({ sub: "s", tenant_id: "bct", all_ou: false, allowed_ou: [2] }, body);
  assert.notEqual(one, two);
});

test("entitlement order does not change the cache key", () => {
  const one = cacheKey({ sub: "s", tenant_id: "bct", all_ou: false, allowed_ou: [1, 2] }, body);
  const two = cacheKey({ sub: "s", tenant_id: "bct", all_ou: false, allowed_ou: [2, 1] }, body);
  assert.equal(one, two, "the same entitlement in a different order is the same entitlement");
});

test("a cached entry expires and is not served after its ttl", async () => {
  cacheClear();
  const key = cacheKey({ sub: "s", tenant_id: "bct", all_ou: true, allowed_ou: [] }, body);
  cacheSet(key, { rows: 1 }, 0.05);
  assert.deepEqual(cacheGet(key), { rows: 1 });
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.equal(cacheGet(key), undefined, "an expired entry must not be served");
});

test("a zero or negative ttl stores nothing", () => {
  cacheClear();
  const key = cacheKey({ sub: "s", tenant_id: "bct", all_ou: true, allowed_ou: [] }, body);
  cacheSet(key, { rows: 1 }, 0);
  assert.equal(cacheGet(key), undefined);
});
