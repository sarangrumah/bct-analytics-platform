import "server-only";

import { toQueryFilters, type PortalFilters } from "./filters";
import type { Session } from "./jwt";
import { query } from "./semantic";

/**
 * The Operating Units this session could narrow to.
 *
 * For a session without the bypass the answer is the entitlement itself, and no query is needed -
 * offering anything else would be offering an option that returns nothing. An EMPTY entitlement
 * yields the single UNASSIGNED member, `-1`, which is a real dimension member and not a missing
 * value: those are the rows such a user is entitled to see, and an earlier version of the
 * warehouse compiler that read the empty case as `IS NULL` would have shown them an empty
 * dashboard forever.
 *
 * Only a session holding `all_ou` needs the list discovered, and then it comes from the semantic
 * layer like everything else.
 */
export async function loadOuOptions(
  session: Session,
  filters: PortalFilters,
): Promise<number[]> {
  if (!session.all_ou) {
    return session.allowed_ou.length === 0 ? [-1] : [...session.allowed_ou];
  }
  const result = await query({
    metric: "revenue_net",
    dimensions: ["operating_unit_id"],
    filters: toQueryFilters(filters, { dateRange: true, operatingUnit: false }),
    order_by: "operating_unit_id",
    limit: 200,
  });
  if (!result.ok) return [];
  const ids: number[] = [];
  for (const row of result.data.rows) {
    const value = row.operating_unit_id;
    if (typeof value === "number" && !ids.includes(value)) ids.push(value);
  }
  return ids;
}
