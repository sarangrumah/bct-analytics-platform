/**
 * Types for contract 06 (`docs/agents/contracts/06-api.md`), hand-written to match the published
 * envelope exactly and pinned to it by `tests/contract-shape.test.mjs`, which parses every file in
 * `analytics/semantic-api/metrics/fixtures/` through the guards below. If Backend regenerates the
 * fixtures with a different shape, that test fails — which is the point of the fixture rule (§2.4).
 *
 * There is no `any` at the API boundary. `unknown` plus a guard is the only way a response becomes
 * a typed value in this codebase.
 */

/** `POST /v1/query` request body. There is no field that carries a tenant, by design. */
export interface QueryRequest {
  metric: string;
  dimensions?: string[];
  filters?: QueryFilters;
  order_by?: string;
  limit?: number;
}

/** Filter values the compiler accepts: a date range pair, an id array, or a string array. */
export type FilterValue = [string, string] | number[] | string[] | string | number;

export type QueryFilters = Record<string, FilterValue>;

/**
 * The `meta` block. `last_refreshed_at` and `is_stale` come from `warehouse.mart_freshness` — real
 * pipeline metadata. Nothing in this application may substitute a clock for them.
 */
export interface QueryMeta {
  tenant_id: string;
  row_count: number;
  last_refreshed_at: string | null;
  is_stale: boolean;
  refresh_sla_seconds: number;
  source_model: string;
  unit: string | null;
  type: string;
  query_duration_ms: number;
  /** Present only when the freshness lookup itself failed. */
  note?: string;
}

/**
 * One result row. Every requested dimension appears as a key; the measure is ALWAYS keyed `value`
 *
 * A dimension cell may be a BOOLEAN as well as a string or a number. Contract 06 section 2 shows
 * only string and numeric dimensions, but `account_balance` declares `is_revenue_line`, and the
 * warehouse returns it as a real JSON boolean. The guard rejected the whole response when it first
 * met one, which took out every panel on the finance view at once - the right failure mode for a
 * shape mismatch, and the reason the guard exists, but the shape is legitimate and the guard was
 * the thing that was wrong.
 * (contract 06 §2), which is why every chart in this app binds to one key and never to a name it
 * has to look up.
 *
 * **`value` is nullable and the distinction is load-bearing.** `revenue_mom_growth` returns `null`
 * for the first month of any window, because that month has no prior month to compare against.
 * "No comparison" and "no growth" are different statements; the API refuses to conflate them, and
 * so does this application. A chart plots a gap and a table prints an em dash - neither prints
 * zero.
 */
export interface QueryRow {
  value: number | null;
  [dimension: string]: string | number | boolean | null;
}

export interface QueryResponse {
  metric: string;
  dimensions: string[];
  rows: QueryRow[];
  meta: QueryMeta;
}

/** One entry from `GET /v1/metrics`. The query UI is built from this, never from a hardcoded list. */
export interface CatalogueMetric {
  name: string;
  label: string;
  description: string;
  grain: string[];
  dimensions: string[];
  filters: Record<string, CatalogueFilterSpec>;
  type: string;
  unit: string | null;
  aggregation: string;
  refresh_sla_seconds: number;
  pdp_class: string;
  source_model?: string;
}

export interface CatalogueFilterSpec {
  type: "daterange" | "int[]" | "string[]" | "int" | "string";
  required: boolean;
  column?: string;
}

export interface Catalogue {
  metrics: CatalogueMetric[];
}

/** Error envelopes from contract 06 §2. */
export interface ApiErrorBody {
  error: string;
  detail: string;
  field?: string;
  available?: string[];
}

/** The verbatim cross-tenant refusal body, from contract 02 and contract 06 §2. */
export const TENANT_SCOPE_VIOLATION: ApiErrorBody = {
  error: "tenant_scope_violation",
  detail: "Session is not scoped to the requested tenant.",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isQueryMeta(value: unknown): value is QueryMeta {
  if (!isRecord(value)) return false;
  return (
    typeof value.tenant_id === "string" &&
    typeof value.row_count === "number" &&
    (typeof value.last_refreshed_at === "string" || value.last_refreshed_at === null) &&
    typeof value.is_stale === "boolean" &&
    typeof value.refresh_sla_seconds === "number" &&
    typeof value.source_model === "string" &&
    (typeof value.unit === "string" || value.unit === null) &&
    typeof value.type === "string" &&
    typeof value.query_duration_ms === "number"
  );
}

export function isQueryRow(value: unknown): value is QueryRow {
  if (!isRecord(value)) return false;
  if (typeof value.value !== "number" && value.value !== null) return false;
  for (const key of Object.keys(value)) {
    const cell = value[key];
    if (
      cell !== null &&
      typeof cell !== "string" &&
      typeof cell !== "number" &&
      typeof cell !== "boolean"
    ) {
      return false;
    }
  }
  return true;
}

export function isQueryResponse(value: unknown): value is QueryResponse {
  if (!isRecord(value)) return false;
  if (typeof value.metric !== "string") return false;
  if (!Array.isArray(value.dimensions) || value.dimensions.some((d) => typeof d !== "string")) {
    return false;
  }
  if (!Array.isArray(value.rows) || !value.rows.every(isQueryRow)) return false;
  return isQueryMeta(value.meta);
}

export function isCatalogue(value: unknown): value is Catalogue {
  if (!isRecord(value)) return false;
  if (!Array.isArray(value.metrics)) return false;
  return value.metrics.every((entry) => {
    if (!isRecord(entry)) return false;
    return (
      typeof entry.name === "string" &&
      typeof entry.label === "string" &&
      Array.isArray(entry.dimensions) &&
      isRecord(entry.filters) &&
      typeof entry.refresh_sla_seconds === "number"
    );
  });
}

export function isApiErrorBody(value: unknown): value is ApiErrorBody {
  return isRecord(value) && typeof value.error === "string" && typeof value.detail === "string";
}
