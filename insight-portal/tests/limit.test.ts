import assert from "node:assert/strict";
import { setTimeout as sleep } from "node:timers/promises";
import { test } from "node:test";

import { inFlightCount, limited } from "../src/lib/limit.ts";

/**
 * The concurrency gate that stops the portal exhausting the semantic API's connection pool.
 *
 * How this was made to go red: `MAX_IN_FLIGHT` was raised to 20 and "never more than four run at
 * once" failed reporting a peak of 10, which is exactly the number of panels on the PPOB view and
 * exactly what produced the upstream 500s.
 */

test("never more than four upstream requests run at once", async () => {
  let peak = 0;
  const tasks = Array.from({ length: 12 }, () =>
    limited(async () => {
      peak = Math.max(peak, inFlightCount());
      await sleep(20);
      return true;
    }),
  );
  await Promise.all(tasks);
  assert.ok(peak <= 4, "peak concurrency was " + peak + "; the semantic API pool holds 8");
  assert.ok(peak > 1, "nothing ran in parallel at all, which would cost the latency budget");
});

test("all twelve still complete, so the gate queues rather than drops", async () => {
  const results = await Promise.all(
    Array.from({ length: 12 }, (_unused, index) => limited(async () => index)),
  );
  assert.deepEqual(results, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
});

test("a rejected task still releases its slot", async () => {
  await assert.rejects(
    limited(async () => {
      throw new Error("upstream failed");
    }),
  );
  assert.equal(inFlightCount(), 0, "a thrown task leaked a slot; the gate would deadlock");
  assert.equal(await limited(async () => "still works"), "still works");
});
