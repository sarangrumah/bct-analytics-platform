import "server-only";

import { cacheGet, cacheKey, cacheSet } from "./cache";
import { config } from "./config";
import { limited } from "./limit";
import { getAccessToken, getSession } from "./session";
import {
  isApiErrorBody,
  isCatalogue,
  isQueryResponse,
  type ApiErrorBody,
  type Catalogue,
  type QueryFilters,
  type QueryRequest,
  type QueryResponse,
} from "./types";

/**
 * The ONLY way this application obtains a number.
 *
 * Three properties are structural rather than remembered:
 *
 *  1. **There is no tenant parameter.** `query()` takes a metric, dimensions, filters, order and
 *     limit — the same fields contract 06 declares — and nothing else. The tenant is read from the
 *     verified session inside this module. A URL parameter, header, cookie or form field cannot
 *     change which tenant is queried because there is no argument through which it could travel.
 *  2. **`server-only` at the top.** Importing this module from a client component is a build error,
 *     so the bearer token cannot end up in a bundle.
 *  3. **No SQL and no arithmetic.** This file forwards a declared metric name and returns the rows
 *     the semantic layer computed. It never sums, divides, or derives. If a figure is missing, that
 *     is a request to Backend, not a line of TypeScript here.
 */

export type QueryResult =
  | { ok: true; data: QueryResponse; cached: boolean }
  | { ok: false; status: number; body: ApiErrorBody };

export interface QuerySpec {
  metric: string;
  dimensions?: string[];
  filters?: QueryFilters;
  order_by?: string;
  limit?: number;
}

async function postJson(
  path: string,
  token: string,
  body: unknown,
): Promise<{ status: number; json: unknown }> {
  return limited(() => postJsonUnlimited(path, token, body));
}

async function postJsonUnlimited(
  path: string,
  token: string,
  body: unknown,
): Promise<{ status: number; json: unknown }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.requestTimeoutMs);
  try {
    const response = await fetch(config.semanticApiUrl + path, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer " + token,
      },
      body: JSON.stringify(body),
      // Never the Next data cache: it is keyed on the request, and `/v1/query` bodies carry no
      // tenant. Caching happens in `cache.ts`, keyed on the verified session.
      cache: "no-store",
      signal: controller.signal,
    });
    const json: unknown = await response.json().catch(() => null);
    return { status: response.status, json };
  } finally {
    clearTimeout(timer);
  }
}

async function getJson(path: string, token: string): Promise<{ status: number; json: unknown }> {
  return limited(() => getJsonUnlimited(path, token));
}

async function getJsonUnlimited(
  path: string,
  token: string,
): Promise<{ status: number; json: unknown }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.requestTimeoutMs);
  try {
    const response = await fetch(config.semanticApiUrl + path, {
      headers: { authorization: "Bearer " + token },
      cache: "no-store",
      signal: controller.signal,
    });
    const json: unknown = await response.json().catch(() => null);
    return { status: response.status, json };
  } finally {
    clearTimeout(timer);
  }
}

const UNAUTHORIZED: ApiErrorBody = { error: "unauthorized", detail: "Invalid token." };
const UPSTREAM: ApiErrorBody = {
  error: "upstream_unavailable",
  detail: "The semantic API did not answer.",
};

/** Run one declared metric query, scoped to the session's tenant. */
export async function query(spec: QuerySpec): Promise<QueryResult> {
  const session = await getSession();
  const token = await getAccessToken();
  if (session === null || token === null) {
    return { ok: false, status: 401, body: UNAUTHORIZED };
  }

  const request: QueryRequest = {
    metric: spec.metric,
    ...(spec.dimensions === undefined ? {} : { dimensions: spec.dimensions }),
    ...(spec.filters === undefined ? {} : { filters: spec.filters }),
    ...(spec.order_by === undefined ? {} : { order_by: spec.order_by }),
    ...(spec.limit === undefined ? {} : { limit: spec.limit }),
  };
  const body = JSON.stringify(request);
  const key = cacheKey(
    {
      sub: session.sub,
      tenant_id: session.tenant_id,
      all_ou: session.all_ou,
      allowed_ou: session.allowed_ou,
    },
    body,
  );

  const hit = cacheGet<QueryResponse>(key);
  if (hit !== undefined) return { ok: true, data: hit, cached: true };

  let status: number;
  let json: unknown;
  try {
    ({ status, json } = await postJson("/v1/query", token, request));
  } catch {
    return { ok: false, status: 503, body: UPSTREAM };
  }

  if (status === 200) {
    if (!isQueryResponse(json)) {
      return {
        ok: false,
        status: 502,
        body: {
          error: "contract_mismatch",
          detail:
            "The semantic API returned a body that does not match contract 06. Refusing to render it.",
        },
      };
    }
    // A response whose tenant is not the session's tenant is never rendered. This cannot happen —
    // the API reads the tenant from the same token — which is exactly why it is asserted rather
    // than assumed: a check that can only fail if something upstream is very wrong is the one worth
    // having.
    if (json.meta.tenant_id !== session.tenant_id) {
      return { ok: false, status: 403, body: {
        error: "tenant_scope_violation",
        detail: "Session is not scoped to the requested tenant.",
      } };
    }
    const ttl = Math.max(
      0,
      Math.min(config.cacheTtlCeilingSeconds, json.meta.refresh_sla_seconds),
    );
    cacheSet(key, json, ttl);
    return { ok: true, data: json, cached: false };
  }

  return {
    ok: false,
    status,
    body: isApiErrorBody(json) ? json : UPSTREAM,
  };
}

/**
 * The metric catalogue. The filter UI and the drill-down validator are built from this, so adding a
 * dimension upstream appears here without a code change (contract 06 §3).
 */
export async function catalogue(): Promise<Catalogue | null> {
  const token = await getAccessToken();
  if (token === null) return null;
  try {
    const { status, json } = await getJson("/v1/metrics", token);
    if (status !== 200 || !isCatalogue(json)) return null;
    return json;
  } catch {
    return null;
  }
}
