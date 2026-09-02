/**
 * A concurrency gate over outbound semantic-API calls.
 *
 * Found by measurement, not by design review. With the aggregate cache disabled, the PPOB view
 * issues ten panel queries at once, and `semantic-api` runs a `ThreadedConnectionPool` with
 * `maxconn=8` over the warehouse. Ten concurrent requests exhaust it, and psycopg2 raises
 * `PoolError: connection pool exhausted`, which surfaces as an HTTP 500. Measured: 133 upstream
 * 500s across a 300-request p95 run, all of them on views with more panels than the pool has
 * connections.
 *
 * Note what that is NOT: it is not the T-1 scope guard. `bct_semantic_pool_guard_trips` stayed at
 * 0 throughout, and the documented 503 `scope_guard` response never appeared. The guard is about a
 * connection carrying a stale tenant; this is simply running out of connections. Two different
 * failures that both involve the word "pool".
 *
 * The portal is the client here, so the portal fixes its own side: no more than four upstream
 * requests in flight from this process at a time. That is comfortably inside the pool even with
 * another consumer (Grafana, a QA run) using it concurrently, and it costs almost nothing - panels
 * still run in parallel, just four at a time instead of ten.
 *
 * The gate lives in `semantic.ts`'s call path rather than in the panel runner, so drill-downs,
 * exports and the Operating Unit lookup are bounded too. A limit that only covers the code path it
 * was written for is a limit that gets bypassed by the next feature.
 */

const MAX_IN_FLIGHT = Number.parseInt(process.env.INSIGHT_PORTAL_MAX_IN_FLIGHT ?? "4", 10);

let inFlight = 0;
const waiting: Array<() => void> = [];

async function acquire(): Promise<void> {
  if (inFlight < MAX_IN_FLIGHT) {
    inFlight += 1;
    return;
  }
  await new Promise<void>((resolve) => waiting.push(resolve));
  inFlight += 1;
}

function release(): void {
  inFlight -= 1;
  const next = waiting.shift();
  if (next !== undefined) next();
}

/** Run `task` with at most `MAX_IN_FLIGHT` others. Always releases, including on rejection. */
export async function limited<T>(task: () => Promise<T>): Promise<T> {
  await acquire();
  try {
    return await task();
  } finally {
    release();
  }
}

/** Test and diagnostics only. */
export function inFlightCount(): number {
  return inFlight;
}
