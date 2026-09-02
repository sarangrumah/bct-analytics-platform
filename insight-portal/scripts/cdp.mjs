/**
 * A minimal Chrome DevTools Protocol client, shared by the screenshot and keyboard-audit scripts.
 *
 * Deliberately not Playwright. This project is scanned by Security's `sca-node` job, and pulling a
 * browser-automation stack into the dependency tree to take screenshots and press Tab would be a
 * permanent addition to that surface for tooling that never ships. Node 24 has a WebSocket client
 * built in, and CDP is a JSON protocol, so this is about a hundred lines with no dependencies.
 */
import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

export const CHROME =
  process.env.CHROME_PATH ??
  "C:/Users/amade/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe";

export function attach(socket) {
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
    /** Evaluate an expression in the page and return its JSON value. */
    async evaluate(expression) {
      const result = await this.send("Runtime.evaluate", {
        expression,
        returnByValue: true,
        awaitPromise: true,
      });
      if (result.exceptionDetails) {
        throw new Error("page threw: " + JSON.stringify(result.exceptionDetails));
      }
      return result.result.value;
    },
  };
}

export async function launch(profileDir) {
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
      "--user-data-dir=" + profileDir,
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
  return { chrome, socket, client: attach(socket) };
}

export async function portalCookie(portal) {
  const password = process.env.BCT_DEV_PASSWORD;
  if (!password) throw new Error("BCT_DEV_PASSWORD is not set");
  const response = await fetch(portal + "/api/auth/login", {
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
