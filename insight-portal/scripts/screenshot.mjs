#!/usr/bin/env node
/**
 * Render evidence: every view at 375 px and at desktop width.
 *
 * Drives headless Chromium over the DevTools Protocol directly, using Node's built-in WebSocket.
 * No Playwright, no Puppeteer, no new dependency - this project is scanned by Security's `sca-node`
 * job and a browser automation stack pulled in to take eight screenshots would be a permanent
 * addition to that surface for a one-off need.
 *
 * The session cookie is injected with `Network.setCookie` rather than by driving the login form,
 * so the shots show the dashboard rather than a login page, and so the run cannot be derailed by a
 * gateway hiccup.
 *
 * Usage: BCT_DEV_PASSWORD=... node scripts/screenshot.mjs [outdir]
 */
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

const PORTAL = process.env.PORTAL_BASE_URL ?? "http://127.0.0.1:33000";
const OUT = process.argv[2] ?? "evidence";
const CHROME =
  process.env.CHROME_PATH ??
  "C:/Users/amade/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe";

const VIEWPORTS = [
  { name: "375px", width: 375, height: 812, mobile: true, scale: 1 },
  { name: "desktop", width: 1440, height: 900, mobile: false, scale: 1 },
];
const VIEWS = ["overview", "sales", "inventory", "finance", "ppob"];

async function login() {
  const password = process.env.BCT_DEV_PASSWORD;
  if (!password) throw new Error("BCT_DEV_PASSWORD is not set");
  const response = await fetch(PORTAL + "/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ login: process.env.BCT_DEV_LOGIN ?? "admin", password, next: "" }),
    redirect: "manual",
  });
  const raw = response.headers
    .getSetCookie()
    .find((entry) => entry.startsWith("insight_portal_session="));
  if (!raw) throw new Error("portal login failed with " + response.status);
  const pair = raw.split(";", 1)[0] ?? "";
  const index = pair.indexOf("=");
  return { name: pair.slice(0, index), value: pair.slice(index + 1) };
}

/** A tiny CDP client: send a command, await the matching id. */
function cdp(socket) {
  let nextId = 1;
  const pending = new Map();
  const listeners = [];
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(String(event.data));
    if (message.id !== undefined && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
      return;
    }
    for (const listener of listeners) listener(message);
  });
  return {
    send(method, params = {}) {
      const id = nextId;
      nextId += 1;
      socket.send(JSON.stringify({ id, method, params }));
      return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    },
    once(method) {
      return new Promise((resolve) => {
        const listener = (message) => {
          if (message.method === method) {
            listeners.splice(listeners.indexOf(listener), 1);
            resolve(message.params);
          }
        };
        listeners.push(listener);
      });
    },
  };
}

const cookie = await login();
mkdirSync(OUT, { recursive: true });

const port = 9222 + Math.floor(Math.random() * 500);
const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--hide-scrollbars",
    "--force-color-profile=srgb",
    "--remote-debugging-port=" + port,
    "--user-data-dir=" + join(OUT, ".chrome-profile"),
    "about:blank",
  ],
  { stdio: ["ignore", "pipe", "pipe"] },
);

let endpoint = null;
for (let attempt = 0; attempt < 60 && endpoint === null; attempt += 1) {
  await sleep(500);
  try {
    const list = await (await fetch("http://127.0.0.1:" + port + "/json/list")).json();
    const page = list.find((target) => target.type === "page");
    if (page) endpoint = page.webSocketDebuggerUrl;
  } catch {
    // not up yet
  }
}
if (endpoint === null) {
  chrome.kill();
  throw new Error("chromium did not expose a debugging endpoint on port " + port);
}

const socket = new WebSocket(endpoint);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});
const client = cdp(socket);

await client.send("Page.enable");
await client.send("Network.enable");
await client.send("Network.setCookie", {
  name: cookie.name,
  value: cookie.value,
  domain: "127.0.0.1",
  path: "/",
  httpOnly: true,
  secure: false,
});

const written = [];
for (const viewport of VIEWPORTS) {
  await client.send("Emulation.setDeviceMetricsOverride", {
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: viewport.scale,
    mobile: viewport.mobile,
  });
  for (const view of VIEWS) {
    const loaded = client.once("Page.loadEventFired");
    await client.send("Page.navigate", { url: PORTAL + "/t/bct/" + view });
    await loaded;
    // Recharts renders on the client; give hydration a moment so the charts are in the shot.
    await sleep(1500);

    const metrics = await client.send("Page.getLayoutMetrics");
    const contentWidth = Math.ceil(metrics.contentSize.width);
    const overflow = contentWidth > viewport.width + 1;

    const shot = await client.send("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: true,
    });
    const file = join(OUT, view + "-" + viewport.name + ".png");
    writeFileSync(file, Buffer.from(shot.data, "base64"));
    written.push({
      file,
      view,
      viewport: viewport.name,
      contentWidth,
      pageHeight: Math.ceil(metrics.contentSize.height),
      horizontalOverflow: overflow,
    });
    console.log(
      [
        view.padEnd(10),
        viewport.name.padEnd(8),
        "content " + contentWidth + "px",
        "height " + Math.ceil(metrics.contentSize.height) + "px",
        overflow ? "HORIZONTAL OVERFLOW" : "no overflow",
      ].join("  "),
    );
  }
}

socket.close();
chrome.kill();

const overflowing = written.filter((entry) => entry.horizontalOverflow);
console.log("");
console.log("wrote " + written.length + " screenshots to " + OUT);
if (overflowing.length > 0) {
  console.log(
    "PAGES THAT SCROLL SIDEWAYS: " +
      overflowing.map((entry) => entry.view + "@" + entry.viewport).join(", "),
  );
  process.exitCode = 1;
} else {
  console.log("no page scrolls sideways at any tested width");
}
