import "server-only";

import type { PanelQuery } from "./panel";
import { query, type QueryResult } from "./semantic";

/**
 * Run a view's panels concurrently.
 *
 * Sequential awaits are the usual reason a server-rendered dashboard misses a latency budget: six
 * panels at 60 ms each is 360 ms in series and 60 ms in parallel, and the difference is entirely
 * self-inflicted. The whole set is awaited inside one Suspense boundary, so the shell has already
 * streamed to the browser by the time any of this starts.
 */
export async function runPanels<K extends string>(
  specs: Record<K, PanelQuery>,
): Promise<Record<K, QueryResult>> {
  const keys = Object.keys(specs) as K[];
  const results = await Promise.all(keys.map((key) => query(specs[key])));
  const out = {} as Record<K, QueryResult>;
  keys.forEach((key, index) => {
    out[key] = results[index]!;
  });
  return out;
}

/** Every `meta` from a set of results, for the view-level freshness banner. */
export function metasOf(results: QueryResult[]): {
  metas: import("./types").QueryMeta[];
} {
  return {
    metas: results.flatMap((result) => (result.ok ? [result.data.meta] : [])),
  };
}
