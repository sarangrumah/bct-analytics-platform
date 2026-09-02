/**
 * The date-range and Operating-Unit filters, and the cookie that makes them persist across views.
 *
 * Persistence is a server-side cookie rather than client state, so navigating from Sales to
 * Inventory keeps the filter with no JavaScript at all — which also means the filter survives a
 * hard reload, a shared link opened later, and a browser with scripting disabled.
 *
 * **The Operating Unit filter narrows; it can never widen.** `semantic-api` applies the session's
 * entitlement predicate (`all_ou`, or `allowed_ou`, or `operating_unit_id = -1` for an empty
 * entitlement) on every query regardless of what is sent here. Selecting an Operating Unit the
 * session is not entitled to therefore returns nothing — it does not grant access to it.
 */

export interface PortalFilters {
  /** ISO date, inclusive. */
  from: string;
  /** ISO date, inclusive. */
  to: string;
  /** Operating Unit ids to narrow to. Empty means "no additional narrowing". */
  ou: number[];
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function isIsoDate(value: string): boolean {
  if (!ISO_DATE.test(value)) return false;
  const parsed = new Date(value + "T00:00:00Z");
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function iso(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/**
 * Default range: the trailing 12 months, which is the window the performance budget is stated
 * against. This uses the server clock, which is legitimate — it is a *filter default*, not a
 * freshness claim. Nothing about "last refreshed at" is derived from any clock anywhere.
 */
export function defaultFilters(now: Date = new Date()): PortalFilters {
  const to = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const from = new Date(to);
  from.setUTCFullYear(from.getUTCFullYear() - 1);
  from.setUTCDate(from.getUTCDate() + 1);
  return { from: iso(from), to: iso(to), ou: [] };
}

export const RANGE_PRESETS: ReadonlyArray<{ id: string; label: string; days: number }> = [
  { id: "30d", label: "30 hari", days: 30 },
  { id: "90d", label: "90 hari", days: 90 },
  { id: "12m", label: "12 bulan", days: 365 },
];

export function presetRange(days: number, now: Date = new Date()): PortalFilters {
  const to = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const from = new Date(to);
  from.setUTCDate(from.getUTCDate() - (days - 1));
  return { from: iso(from), to: iso(to), ou: [] };
}

/** Parse the persisted cookie. Anything malformed falls back to the default — never to an error. */
export function parseFilters(raw: string | undefined, now: Date = new Date()): PortalFilters {
  const base = defaultFilters(now);
  if (raw === undefined || raw === "") return base;
  const params = new URLSearchParams(raw);
  const from = params.get("from");
  const to = params.get("to");
  const ouRaw = params.get("ou");
  const next: PortalFilters = { ...base };
  if (from !== null && to !== null && isIsoDate(from) && isIsoDate(to) && from <= to) {
    next.from = from;
    next.to = to;
  }
  if (ouRaw !== null && ouRaw !== "") {
    const ids = ouRaw
      .split(",")
      .map((part) => Number.parseInt(part, 10))
      .filter((id) => Number.isInteger(id));
    next.ou = ids;
  }
  return next;
}

export function serialiseFilters(filters: PortalFilters): string {
  const params = new URLSearchParams();
  params.set("from", filters.from);
  params.set("to", filters.to);
  if (filters.ou.length > 0) params.set("ou", filters.ou.join(","));
  return params.toString();
}

/**
 * Build the `filters` object for `/v1/query`.
 *
 * `date_range` is omitted for metrics that do not declare it — `stock_net_quantity` reads
 * `mart_stock_position`, which is a position and not a daily series, so sending a range would be a
 * 400 (contract 06 §8, constraint 3).
 */
export function toQueryFilters(
  filters: PortalFilters,
  options: { dateRange: boolean; operatingUnit?: boolean } = { dateRange: true },
): Record<string, [string, string] | number[]> {
  const out: Record<string, [string, string] | number[]> = {};
  if (options.dateRange) out.date_range = [filters.from, filters.to];
  if (options.operatingUnit !== false && filters.ou.length > 0) {
    out.operating_unit_id = filters.ou;
  }
  return out;
}

/** The same window, shifted back one year, for a year-on-year comparison. */
export function priorYear(filters: PortalFilters): { from: string; to: string } {
  const shift = (value: string): string => {
    const date = new Date(value + "T00:00:00Z");
    date.setUTCFullYear(date.getUTCFullYear() - 1);
    return iso(date);
  };
  return { from: shift(filters.from), to: shift(filters.to) };
}
