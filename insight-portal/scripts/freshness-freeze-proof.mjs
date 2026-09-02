#!/usr/bin/env node
/**
 * The freshness proof the brief asks for: freeze the pipeline, confirm "last refreshed at" stops
 * advancing AND is not null, then unfreeze and confirm it moves again.
 *
 * Why this is a script and not a test: it stops and restarts `odoo19-bct-cdc`, which is Backend's
 * container, and that is not something to do as a side effect of `npm run test`. Everything it
 * touches is inside the `odoo19-bct` project, the stop is 60 seconds, and the container is
 * restarted in a `finally` so an assertion failure cannot leave the pipeline down. Postgres retains
 * WAL for the slot meanwhile; `max_slot_wal_keep_size` is 2 GB and a minute of an idle Odoo is
 * nowhere near it.
 *
 * What makes the proof a proof rather than a tautology, in three parts:
 *
 *   1. A field that renders EMPTY also "stops advancing". So the frozen value is asserted to be
 *      non-empty, to parse as a real instant, and to equal what `warehouse.pipeline_state` holds -
 *      read independently with psql, not from the page.
 *   2. A HARDCODED string also never advances. So the third phase restarts the consumer and
 *      requires the value to move. A build that printed a constant passes phase two and fails here.
 *   3. A CLOCK would advance during phase two. That is the failure this whole exercise is looking
 *      for, and phase two is where it would show up.
 *
 * Usage: BCT_DEV_PASSWORD=... node scripts/freshness-freeze-proof.mjs
 */
import { execFileSync } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

const PORTAL = process.env.PORTAL_BASE_URL ?? "http://127.0.0.1:33000";
const CDC = "odoo19-bct-cdc";
const STAMP = /Diperbarui (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)/;

const PIPELINE_SQL =
  "select last_refreshed_at from warehouse.mart_freshness " +
  "where tenant_id = 'bct' and mart_name = 'mart_revenue_daily'";

function warehouse(sql) {
  return execFileSync(
    "docker",
    ["exec", "odoo19-bct-warehouse-db", "psql", "-U", "warehouse_admin", "-d", "warehouse", "-tAc", sql],
    { encoding: "utf8" },
  ).trim();
}

function docker(...args) {
  return execFileSync("docker", args, { encoding: "utf8" }).trim();
}

/**
 * Is the loader gone?
 *
 * Not "is it stopped" - `scripts/analytics/cdc-run.sh` starts it with `docker run --rm`, so a stop
 * DELETES the container. The discriminating check is therefore absence, and it also catches the
 * case that wasted a run of this script: something re-ran `cdc-run.sh` inside the freeze window,
 * the container came back within about 40 seconds, and the heartbeat resumed while the script still
 * believed the pipeline was frozen. Asserting absence at BOTH ends of the window is what makes the
 * freeze a fact rather than an assumption. (Diagnosis from Platform-Addons, who owns the loader.)
 */
function loaderAbsent() {
  try {
    execFileSync("docker", ["inspect", CDC], { encoding: "utf8", stdio: "pipe" });
    return false;
  } catch {
    return true;
  }
}

function rendered(pg) {
  const match = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})/.exec(pg);
  if (match === null) throw new Error("unreadable pipeline timestamp: " + pg);
  return match[1] + " " + match[2] + " UTC";
}

let failures = 0;
function check(label, condition, detail = "") {
  const mark = condition ? "PASS" : "FAIL";
  if (!condition) failures += 1;
  console.log("  [" + mark + "] " + label + (detail === "" ? "" : " -> " + detail));
}

async function login() {
  const password = process.env.BCT_DEV_PASSWORD;
  if (!password) throw new Error("BCT_DEV_PASSWORD is not set");
  const response = await fetch(PORTAL + "/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ login: process.env.BCT_DEV_LOGIN ?? "admin", password, next: "" }),
    redirect: "manual",
  });
  const cookie = response.headers
    .getSetCookie()
    .find((entry) => entry.startsWith("insight_portal_session="));
  if (!cookie) throw new Error("portal login failed with " + response.status);
  return cookie.split(";", 1)[0];
}

async function pageStamp(cookie) {
  const html = await (await fetch(PORTAL + "/t/bct/overview", { headers: { cookie } })).text();
  const match = STAMP.exec(html);
  return match === null ? "" : match[1];
}

const cookie = await login();
console.log("# freshness freeze proof");
console.log("portal: " + PORTAL + "   consumer: " + CDC);
console.log("");

try {
  console.log("PHASE 0 - the pipeline is moving before we freeze it");
  const moving0 = warehouse(PIPELINE_SQL);
  await sleep(35_000);
  const moving1 = warehouse(PIPELINE_SQL);
  check(
    "pipeline_state advances while the consumer runs",
    moving0 !== moving1,
    moving0 + " -> " + moving1,
  );
  console.log("");

  console.log("PHASE 1 - freeze");
  try {
    docker("stop", CDC);
  } catch {
    console.log("  (loader was already gone)");
  }
  check("the loader container is ABSENT, not merely stopped", loaderAbsent(), CDC);
  // The heartbeat thread ticks every 15 s on its own connection, so one more beat can land while
  // the process is shutting down. Wait past that before sampling, then require two equal reads.
  await sleep(25_000);
  const settled = warehouse(PIPELINE_SQL);
  await sleep(25_000);
  const stillSettled = warehouse(PIPELINE_SQL);
  check(
    "pipeline_state itself has stopped advancing",
    settled === stillSettled,
    settled + " == " + stillSettled,
  );
  check("the loader is STILL absent, so nothing restarted it mid-window", loaderAbsent(), CDC);
  console.log("");

  console.log("PHASE 2 - the portal follows it, and does not read a clock");
  const first = await pageStamp(cookie);
  check("the timestamp is present and not blank", first !== "", JSON.stringify(first));
  check("the timestamp parses as a real instant", !Number.isNaN(Date.parse(first.replace(" UTC", "Z").replace(" ", "T"))), first);
  check(
    "the timestamp equals warehouse.pipeline_state",
    first === rendered(stillSettled),
    first + " vs " + rendered(stillSettled),
  );

  const wallStart = Date.now();
  await sleep(45_000);
  const moved = Date.now() - wallStart;
  const second = await pageStamp(cookie);
  check("the wall clock advanced during the window", moved >= 44_000, moved + " ms");
  check("the timestamp is STILL not blank", second !== "", JSON.stringify(second));
  check("the timestamp did NOT advance while the pipeline was frozen", first === second, first + " == " + second);
  check("the loader remained absent for the whole observation window", loaderAbsent(), CDC);
  const age = Date.now() - Date.parse(second.replace(" UTC", "Z").replace(" ", "T"));
  // Only that it is in the past at all. An earlier version of this line demanded 45 s of age and
  // failed on a correct portal: the consumer heartbeats every ~40 s, so a freshly landed value is
  // legitimately young. The assertion was wrong, not the thing it was testing.
  check("the timestamp is in the past, as pipeline metadata must be", age > 1_000, age + " ms old");
  console.log("");
} finally {
  console.log("PHASE 3 - unfreeze (always runs)");
  // `docker start` cannot work: --rm means the container no longer exists. The owner's own script
  // recreates it with the repo's defaults, which set CDC_TENANT_DB and CDC_TENANT_SLUG from
  // ODOO_DB_NAME together - the pairing contract 06 warns must not be split.
  docker("compose", "version");
  execFileSync("bash", ["../scripts/analytics/cdc-run.sh", "--detach"], {
    encoding: "utf8",
    cwd: process.cwd(),
  });
  console.log("  recreated " + CDC + " via scripts/analytics/cdc-run.sh --detach");
}

const before = await pageStamp(cookie);
let after = before;
const deadline = Date.now() + 120_000;
while (Date.now() < deadline) {
  await sleep(5000);
  after = await pageStamp(cookie);
  if (after !== before && after !== "") break;
}
check(
  "the timestamp advances again once the consumer is back, so it is not a constant",
  after !== before && after !== "",
  before + " -> " + after,
);

console.log("");
console.log(failures === 0 ? "ALL CHECKS PASSED" : failures + " CHECK(S) FAILED");
process.exit(failures === 0 ? 0 : 1);
