#!/usr/bin/env node
/**
 * Latency measurement for the p95 budget.
 *
 * Method, stated because the number is meaningless without it:
 *
 *  - The client is this script on the same host as the server, over loopback. That deliberately
 *    excludes network transit: what is being measured is the application and the warehouse behind
 *    it, not the operator's connection.
 *  - Requests are SEQUENTIAL, one at a time, which is the single-user dashboard-load model the
 *    budget describes. A concurrent run measures throughput, which is a different question.
 *  - Each sample is time to LAST byte of the full HTML document, not to first byte. The shell
 *    streams early, so a TTFB figure would flatter the result by reporting when the navigation bar
 *    arrived rather than when the figures did. TTFB is recorded alongside so the streaming gap is
 *    visible rather than hidden.
 *  - Warm-up requests are run and DISCARDED, because the first request after a start pays for JIT,
 *    the JWKS fetch and the first connection to the semantic API. Those are real costs but they are
 *    not what a p95 over a working day looks like. The cold figure is reported separately.
 *  - p95 is the nearest-rank order statistic: the value at ceil(0.95 * n), one-indexed. With
 *    n = 60 that is the 57th slowest sample. No interpolation, no smoothing.
 *
 * Run it twice to separate the application from its cache:
 *   INSIGHT_PORTAL_CACHE_TTL_SECONDS=0  ... every panel misses the aggregate cache
 *   INSIGHT_PORTAL_CACHE_TTL_SECONDS=30 ... the shipped default
 */
import { cpus, totalmem, platform, release } from "node:os";

const PORTAL = process.env.PORTAL_BASE_URL ?? "http://127.0.0.1:33000";
const SAMPLES = Number.parseInt(process.env.SAMPLES ?? "60", 10);
const WARMUP = Number.parseInt(process.env.WARMUP ?? "5", 10);
const LABEL = process.env.LABEL ?? "unlabelled";

const VIEWS = ["overview", "sales", "inventory", "finance", "ppob"];

function quantile(sorted, q) {
  if (sorted.length === 0) return Number.NaN;
  const rank = Math.ceil(q * sorted.length);
  return sorted[Math.min(sorted.length, Math.max(1, rank)) - 1];
}

async function login() {
  const password = process.env.BCT_DEV_PASSWORD;
  if (!password) {
    console.error(
      "BCT_DEV_PASSWORD is not set. Refusing to measure: an unauthenticated request is a redirect\n" +
        "to /login, which would produce a very fast p95 that measures nothing.",
    );
    process.exit(2);
  }
  const response = await fetch(PORTAL + "/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      login: process.env.BCT_DEV_LOGIN ?? "admin",
      password,
      next: "",
    }),
    redirect: "manual",
  });
  const cookie = response.headers
    .getSetCookie()
    .find((entry) => entry.startsWith("insight_portal_session="));
  if (!cookie) {
    console.error("login failed with status " + response.status + "; nothing to measure");
    process.exit(2);
  }
  return cookie.split(";", 1)[0];
}

async function sample(url, cookie) {
  const started = process.hrtime.bigint();
  const response = await fetch(url, { headers: { cookie } });
  const firstByte = process.hrtime.bigint();
  const body = await response.text();
  const done = process.hrtime.bigint();
  return {
    status: response.status,
    bytes: body.length,
    ttfb: Number(firstByte - started) / 1e6,
    total: Number(done - started) / 1e6,
    failedPanels: (body.match(/Panel gagal dimuat/g) ?? []).length,
  };
}

const cookie = await login();

console.log("# insight-portal latency, label=" + LABEL);
console.log("host: " + platform() + " " + release() + ", " + cpus().length + " logical CPUs, " +
  (totalmem() / 1024 ** 3).toFixed(1) + " GiB RAM");
console.log("node: " + process.versions.node);
console.log("target: " + PORTAL + " (loopback, sequential, single client)");
console.log("samples: " + SAMPLES + " measured after " + WARMUP + " discarded warm-ups, per view");
console.log("statistic: nearest-rank, p95 = ceil(0.95*n)th slowest sample");
console.log("");
console.log(
  ["view", "n", "cold_ms", "min", "p50", "p95", "p99", "max", "ttfb_p95", "kb", "fail"].join("\t"),
);

let worstP95 = 0;
for (const view of VIEWS) {
  const url = PORTAL + "/t/bct/" + view;
  const cold = await sample(url, cookie);
  for (let index = 1; index < WARMUP; index += 1) await sample(url, cookie);

  const totals = [];
  const ttfbs = [];
  let bytes = 0;
  let failed = 0;
  let bad = 0;
  for (let index = 0; index < SAMPLES; index += 1) {
    const result = await sample(url, cookie);
    if (result.status !== 200) bad += 1;
    failed += result.failedPanels;
    bytes = result.bytes;
    totals.push(result.total);
    ttfbs.push(result.ttfb);
  }
  totals.sort((a, b) => a - b);
  ttfbs.sort((a, b) => a - b);
  const p95 = quantile(totals, 0.95);
  worstP95 = Math.max(worstP95, p95);

  console.log(
    [
      view,
      SAMPLES,
      cold.total.toFixed(0),
      totals[0].toFixed(0),
      quantile(totals, 0.5).toFixed(0),
      p95.toFixed(0),
      quantile(totals, 0.99).toFixed(0),
      totals[totals.length - 1].toFixed(0),
      quantile(ttfbs, 0.95).toFixed(0),
      (bytes / 1024).toFixed(0),
      failed + (bad > 0 ? " (" + bad + " non-200)" : ""),
    ].join("\t"),
  );
}

console.log("");
console.log("worst p95 across the five views: " + worstP95.toFixed(0) + " ms");
console.log("budget: 2000 ms -> " + (worstP95 < 2000 ? "MET" : "MISSED"));
if (worstP95 >= 2000) process.exitCode = 1;
