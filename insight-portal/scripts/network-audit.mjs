#!/usr/bin/env node
/**
 * Acceptance criterion 4: no network request from the browser reaches the warehouse, Odoo, or the
 * semantic API directly. Demonstrated by listing the browser's actual requests rather than by
 * asserting the architecture.
 *
 * Every request the page makes is recorded from `Network.requestWillBeSent`, so this covers
 * fetch/XHR started by hydrated JavaScript as well as the document, scripts and styles - the ones
 * an architecture diagram would miss.
 *
 * Usage: BCT_DEV_PASSWORD=... node scripts/network-audit.mjs
 */
import { rmSync } from "node:fs";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

import { launch, portalCookie } from "./cdp.mjs";

const PORTAL = process.env.PORTAL_BASE_URL ?? "http://127.0.0.1:33000";
const VIEWS = ["overview", "sales", "inventory", "finance", "ppob", "drill"];
const PROFILE = join("evidence", ".chrome-network");

// Anything the browser must never be seen talking to.
const FORBIDDEN = [
  { label: "semantic-api", pattern: /:38200/ },
  { label: "login-gateway", pattern: /:38120/ },
  { label: "warehouse postgres", pattern: /:35433/ },
  { label: "odoo OLTP postgres", pattern: /:35432/ },
  { label: "odoo http", pattern: /:38069|:38072/ },
  { label: "a postgres connection string", pattern: /postgres(ql)?:\/\// },
];

const cookie = await portalCookie(PORTAL);
rmSync(PROFILE, { recursive: true, force: true });
const { chrome, socket, client } = await launch(PROFILE);

const requests = [];
await client.send("Network.enable");
await client.send("Page.enable");
socket.addEventListener("message", (event) => {
  const message = JSON.parse(String(event.data));
  if (message.method === "Network.requestWillBeSent") {
    requests.push(message.params.request.url);
  }
});
await client.send("Network.setCookie", {
  name: cookie.name,
  value: cookie.value,
  domain: "127.0.0.1",
  path: "/",
  httpOnly: true,
  secure: false,
});
await client.send("Emulation.setDeviceMetricsOverride", {
  width: 1440,
  height: 900,
  deviceScaleFactor: 1,
  mobile: false,
});

for (const view of VIEWS) {
  const url =
    view === "drill"
      ? PORTAL + "/t/bct/drill?metric=revenue_net&by=date_day,revenue_channel&order=-value&limit=100"
      : PORTAL + "/t/bct/" + view;
  const loaded = client.once("Page.loadEventFired");
  await client.send("Page.navigate", { url });
  await loaded;
  // Let hydration run and any client-side fetch fire.
  await sleep(2500);
}

socket.close();
chrome.kill();
rmSync(PROFILE, { recursive: true, force: true });

const origins = new Map();
for (const url of requests) {
  let origin = url;
  try {
    origin = new URL(url).origin;
  } catch {
    origin = url.slice(0, 40);
  }
  origins.set(origin, (origins.get(origin) ?? 0) + 1);
}

console.log("# every request the browser made across " + VIEWS.length + " views");
console.log("");
for (const [origin, count] of [...origins.entries()].sort((a, b) => b[1] - a[1])) {
  console.log("  " + String(count).padStart(4) + "  " + origin);
}

console.log("");
let failures = 0;
for (const rule of FORBIDDEN) {
  const hits = requests.filter((url) => rule.pattern.test(url));
  if (hits.length > 0) failures += 1;
  console.log(
    "  [" + (hits.length === 0 ? "PASS" : "FAIL") + "] browser never reaches " + rule.label +
      (hits.length === 0 ? "" : " -> " + hits.slice(0, 3).join(", ")),
  );
}

const offOrigin = requests.filter((url) => !url.startsWith(PORTAL) && !url.startsWith("data:"));
if (offOrigin.length > 0) failures += 1;
console.log(
  "  [" + (offOrigin.length === 0 ? "PASS" : "FAIL") +
    "] every request goes to this application's own origin" +
    (offOrigin.length === 0 ? "" : " -> " + offOrigin.slice(0, 5).join(", ")),
);

console.log("");
console.log(failures === 0 ? "ALL NETWORK CHECKS PASSED" : failures + " CHECK(S) FAILED");
process.exit(failures === 0 ? 0 : 1);
