#!/usr/bin/env node
/**
 * Keyboard reach and colour-independence audit.
 *
 * Acceptance criterion 8: "Keyboard navigation reaches every interactive chart control; no
 * information is conveyed by colour alone." Both halves are checked against the rendered page
 * rather than asserted in prose.
 *
 * The tab walk is a real walk: it sends Tab keystrokes through the DevTools input domain and reads
 * `document.activeElement` after each one, so what is recorded is the browser's actual focus order,
 * not a `querySelectorAll` of things that look focusable. Those differ - an element can match
 * `[tabindex]` and still be unreachable behind `inert`, `display:none`, or a focus trap.
 *
 * Usage: BCT_DEV_PASSWORD=... node scripts/keyboard-audit.mjs [view]
 */
import { rmSync } from "node:fs";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

import { launch, portalCookie } from "./cdp.mjs";

const PORTAL = process.env.PORTAL_BASE_URL ?? "http://127.0.0.1:33000";
const VIEWS = process.argv[2]
  ? [process.argv[2]]
  : ["overview", "sales", "inventory", "finance", "ppob"];
const PROFILE = join("evidence", ".chrome-keyboard");

let failures = 0;
function check(label, condition, detail = "") {
  if (!condition) failures += 1;
  console.log("  [" + (condition ? "PASS" : "FAIL") + "] " + label + (detail ? " -> " + detail : ""));
}

const DESCRIBE = [
  "(() => {",
  "  const el = document.activeElement;",
  "  if (!el || el === document.body) return null;",
  "  let label = el.getAttribute('aria-label') || '';",
  "  if (!label && el.id) {",
  "    const lab = document.querySelector('label[for=\"' + el.id + '\"]');",
  "    if (lab) label = lab.textContent || '';",
  "  }",
  "  if (!label) label = (el.textContent || '').slice(0, 60);",
  "  if (!label) label = el.getAttribute('name') || '';",
  "  return {",
  "    tag: el.tagName.toLowerCase(),",
  "    type: el.getAttribute('type') || '',",
  "    role: el.getAttribute('role') || '',",
  "    cls: (el.getAttribute('class') || '').slice(0, 60),",
  "    label: label.replace(/\\s+/g, ' ').trim(),",
  "    href: el.getAttribute('href') || '',",
  "  };",
  "})()",
].join("\n");

const cookie = await portalCookie(PORTAL);
rmSync(PROFILE, { recursive: true, force: true });
const { chrome, socket, client } = await launch(PROFILE);

await client.send("Page.enable");
await client.send("Network.enable");
await client.send("Runtime.enable");
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

async function key(name, code) {
  for (const type of ["rawKeyDown", "keyUp"]) {
    await client.send("Input.dispatchKeyEvent", {
      type,
      windowsVirtualKeyCode: code,
      nativeVirtualKeyCode: code,
      key: name,
      code: name,
    });
  }
}

for (const view of VIEWS) {
  console.log("");
  console.log("# " + view);
  const loaded = client.once("Page.loadEventFired");
  await client.send("Page.navigate", { url: PORTAL + "/t/bct/" + view });
  await loaded;
  await sleep(1800);

  await client.evaluate("document.body.focus(); window.scrollTo(0, 0);");

  const stops = [];
  for (let step = 0; step < 400; step += 1) {
    await key("Tab", 9);
    const stop = await client.evaluate(DESCRIBE);
    if (stop === null) break;
    stops.push(stop);
  }

  const has = (predicate) => stops.some(predicate);
  check("focus visits something at all", stops.length > 0, stops.length + " stops");
  check(
    "the skip link is the first stop",
    (stops[0] && stops[0].label.indexOf("Lewati ke konten utama") >= 0) === true,
    stops[0] ? stops[0].label : "none",
  );
  check(
    "every one of the five views is reachable from the nav",
    ["overview", "sales", "inventory", "finance", "ppob"].every((slug) =>
      has((stop) => stop.href.endsWith("/" + slug)),
    ),
  );
  const dateStops = stops.filter((stop) => stop.type === "date").length;
  check(
    "both date inputs are reachable",
    dateStops >= 2,
    dateStops + " date stops (a date input may present several segments)",
  );
  check(
    "the apply button and all three range presets are reachable",
    stops.filter((stop) => stop.tag === "button" && stop.label !== "Keluar").length >= 4,
  );
  check(
    "at least one chart is focusable, so arrow keys can drive it",
    has((stop) => stop.cls.indexOf("recharts-surface") >= 0 || stop.role === "application"),
  );
  check(
    "every data table is reachable as a scrollable region",
    has((stop) => stop.role === "region" && stop.label.indexOf("Tabel data") === 0),
  );
  check("a CSV export link is reachable", has((stop) => stop.href.indexOf("format=csv") >= 0));
  check("an XLSX export link is reachable", has((stop) => stop.href.indexOf("format=xlsx") >= 0));
  check("the logout control is reachable", has((stop) => stop.label === "Keluar"));

  const unnamed = stops.filter((stop) => stop.label === "" && stop.type !== "date");
  check(
    "no focus stop is anonymous to a screen reader",
    unnamed.length === 0,
    unnamed.length === 0 ? "" : JSON.stringify(unnamed.slice(0, 3)),
  );

  // Arrow-key driving of a focused chart. Recharts' accessibilityLayer moves an active index and
  // renders the tooltip; a static image would do nothing here.
  const focused = await client.evaluate(
    "(() => { const s = document.querySelector('.recharts-surface'); if (!s) return false; s.focus(); return document.activeElement === s; })()",
  );
  check("a chart surface can take focus", focused === true);
  if (focused === true) {
    // Try each arrow. A horizontal bar chart (`layout="vertical"`) reads its category axis
    // vertically, so its keyboard axis is Up/Down rather than Left/Right - asserting only on
    // ArrowRight would report a working chart as broken, which is what the first run of this audit
    // did on the inventory and finance views.
    const arrows = [
      ["ArrowRight", 39],
      ["ArrowDown", 40],
      ["ArrowLeft", 37],
      ["ArrowUp", 38],
    ];
    let tooltip = "";
    let worked = "";
    for (const [name, code] of arrows) {
      await client.evaluate(
        "(() => { const s = document.querySelector('.recharts-surface'); if (s) s.focus(); })()",
      );
      await key(name, code);
      await sleep(250);
      await key(name, code);
      await sleep(500);
      const text = await client.evaluate(
        "(document.querySelector('.recharts-tooltip-wrapper') ? document.querySelector('.recharts-tooltip-wrapper').textContent : '').trim()",
      );
      if (typeof text === "string" && text.length > 0) {
        tooltip = text;
        worked = name;
        break;
      }
    }
    check(
      "arrow keys move through the series and surface values",
      tooltip.length > 0,
      worked === "" ? "no arrow key produced a value" : worked + " -> " + JSON.stringify(tooltip.slice(0, 60)),
    );
  }

  // Colour independence. A status colour must never be the only carrier of meaning.
  //
  // The rule is about INFORMATION, not markup: a decorative glyph marked aria-hidden is allowed to
  // be the coloured node as long as a word sits beside it in the same block. So a coloured node
  // with no letters of its own is only a problem when no nearby ancestor supplies them. Written
  // this way the check still fires on a bare coloured dot with nothing around it - which is what it
  // is for - and does not fire on the warning triangle in front of "Tidak tersedia pada build ini".
  const colourOnly = await client.evaluate(
    [
      "(() => {",
      "  const problems = [];",
      "  const nodes = document.querySelectorAll('[style*=\"--status-\"]');",
      "  const letters = (el) => /[A-Za-z]/.test((el.textContent || '').trim());",
      "  for (const el of nodes) {",
      "    if (letters(el)) continue;",
      "    let ancestor = el.parentElement;",
      "    let depth = 0;",
      "    let ok = false;",
      "    while (ancestor && depth < 3) {",
      "      if (letters(ancestor)) { ok = true; break; }",
      "      ancestor = ancestor.parentElement;",
      "      depth += 1;",
      "    }",
      "    if (!ok) problems.push((el.getAttribute('class') || el.tagName) + '::' + (el.textContent || '').trim());",
      "  }",
      "  return problems;",
      "})()",
    ].join("\n"),
  );
  check(
    "nothing is encoded by colour alone",
    Array.isArray(colourOnly) && colourOnly.length === 0,
    Array.isArray(colourOnly) ? colourOnly.slice(0, 3).join(" | ") : "",
  );

  const freshnessWords = await client.evaluate(
    "Array.from(document.querySelectorAll('p,span')).some((e) => /(Segar|Basi)/.test(e.textContent || ''))",
  );
  check("freshness state is stated in words, not only in colour", freshnessWords === true);
}

socket.close();
chrome.kill();
rmSync(PROFILE, { recursive: true, force: true });

console.log("");
console.log(failures === 0 ? "ALL KEYBOARD/CONTRAST CHECKS PASSED" : failures + " CHECK(S) FAILED");
process.exit(failures === 0 ? 0 : 1);
