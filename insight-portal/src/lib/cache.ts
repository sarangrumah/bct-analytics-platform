/**
 * A small in-process cache for aggregate responses.
 *
 * Why the key shape is the interesting part: a cache that is keyed only on the request body would
 * serve one tenant's rows to another, because `/v1/query` bodies carry no tenant — the tenant comes
 * from the token. So the key is built from the VERIFIED SESSION as well as the request:
 * `sub | tenant_id | all_ou | allowed_ou | body`. Two sessions that differ in tenant, in the
 * Operating Unit bypass, or in their Operating Unit entitlement can never collide.
 *
 * `tests/cache-partition.test.mjs` asserts exactly that, and was observed to fail when the
 * `tenant_id` segment was removed from the template.
 *
 * TTL is bounded by the metric's own `refresh_sla_seconds`, so a cached value can never be older
 * than the freshness contract the panel advertises — and `meta.last_refreshed_at` is cached with
 * the rows, so a cached panel reports the pipeline timestamp it actually came from rather than the
 * time it was served.
 */

export interface CacheIdentity {
  sub: string;
  tenant_id: string;
  all_ou: boolean;
  allowed_ou: number[];
}

/** The key template. Exported so a test can assert its parts rather than infer them. */
export function cacheKey(identity: CacheIdentity, body: string): string {
  const ou = [...identity.allowed_ou].sort((a, b) => a - b).join(",");
  return [identity.sub, identity.tenant_id, identity.all_ou ? "all" : "scoped", ou, body].join("|");
}

interface Entry<T> {
  value: T;
  expiresAt: number;
}

const MAX_ENTRIES = 500;
const store = new Map<string, Entry<unknown>>();

export function cacheGet<T>(key: string): T | undefined {
  const hit = store.get(key);
  if (hit === undefined) return undefined;
  if (hit.expiresAt <= Date.now()) {
    store.delete(key);
    return undefined;
  }
  // Refresh recency for the crude LRU below.
  store.delete(key);
  store.set(key, hit);
  return hit.value as T;
}

export function cacheSet<T>(key: string, value: T, ttlSeconds: number): void {
  if (ttlSeconds <= 0) return;
  if (store.size >= MAX_ENTRIES) {
    const oldest = store.keys().next();
    if (!oldest.done) store.delete(oldest.value);
  }
  store.set(key, { value, expiresAt: Date.now() + ttlSeconds * 1000 });
}

/** Test and logout support: drop everything. */
export function cacheClear(): void {
  store.clear();
}

export function cacheSize(): number {
  return store.size;
}
