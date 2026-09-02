import assert from "node:assert/strict";
import { test } from "node:test";

import { LIVE, PORTAL, credentials } from "./live-helpers.ts";

/**
 * Redirects, followed rather than counted.
 *
 * This file exists because a redirect shipped broken and the whole suite passed. Login answered
 * `303 See Other` with `location: http://0.0.0.0:3000/t/bct/overview` - the bind address, which a
 * browser resolves verbatim and fails on with ERR_ADDRESS_INVALID. The session cookie was set
 * correctly and every view returned 200 with it, so the tests, which carry the cookie and request
 * views directly, never touched the broken path. I recorded `login=303` as evidence and the Lead
 * read it and agreed; neither of us looked at the header.
 *
 * **A redirect status code is not evidence of a working redirect.** So every assertion here does
 * what a browser does: read `Location`, resolve it, and GO THERE. A test that stops at the status
 * line cannot tell 303-to-somewhere-good from 303-to-nowhere.
 *
 * How these were made to go red: reverting `redirectTo()` to `new URL(target, request.url)` and
 * running against the container. Every test in this file failed with `0.0.0.0` in the location,
 * while every other test in the suite stayed green - which is precisely the blind spot being closed.
 */

const describe = LIVE ? test : test.skip;

/** Addresses a Location must never contain. A bind address is not a destination. */
const UNREACHABLE = ["0.0.0.0", "[::]"];

function assertUsable(location: string | null, label: string): string {
  assert.notEqual(location, null, label + ": no Location header at all");
  const value = location ?? "";
  for (const bad of UNREACHABLE) {
    assert.equal(
      value.includes(bad),
      false,
      label + ": Location points at the bind address, not a reachable host -> " + value,
    );
  }
  // Resolve exactly as a browser would: absolute wins, otherwise relative to the request.
  const resolved = new URL(value, PORTAL);
  assert.equal(
    resolved.origin,
    new URL(PORTAL).origin,
    label + ": Location leaves this origin -> " + resolved.href,
  );
  return resolved.href;
}

async function login(): Promise<{ cookie: string; location: string }> {
  const { login: user, password } = credentials();
  const response = await fetch(PORTAL + "/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ login: user, password, next: "" }),
    redirect: "manual",
  });
  assert.equal(response.status, 303, "login did not answer 303");
  const cookie =
    response.headers
      .getSetCookie()
      .find((entry) => entry.startsWith("insight_portal_session="))
      ?.split(";", 1)[0] ?? "";
  assert.notEqual(cookie, "", "login set no session cookie");
  return { cookie, location: assertUsable(response.headers.get("location"), "login") };
}

describe("redirect: a successful login sends the browser somewhere it can actually reach", async () => {
  const { cookie, location } = await login();

  // The status was already asserted. This is the part that was missing: go there.
  const followed = await fetch(location, { headers: { cookie } });
  assert.equal(followed.status, 200, "the login redirect target did not serve: " + location);
  const html = await followed.text();
  assert.ok(
    html.includes("Ringkasan Eksekutif"),
    "the login redirect landed somewhere that is not the overview: " + location,
  );
});

describe("redirect: the whole browser journey ends on a real address", async () => {
  const { login: user, password } = credentials();
  const first = await fetch(PORTAL + "/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ login: user, password, next: "" }),
    redirect: "manual",
  });
  const cookie =
    first.headers
      .getSetCookie()
      .find((entry) => entry.startsWith("insight_portal_session="))
      ?.split(";", 1)[0] ?? "";
  const target = assertUsable(first.headers.get("location"), "login");
  const second = await fetch(target, { headers: { cookie }, redirect: "follow" });
  assert.equal(second.status, 200);
  assert.equal(
    second.url.includes("0.0.0.0"),
    false,
    "the browser ended up at a bind address: " + second.url,
  );
});

describe("redirect: a failed login goes to a reachable login page", async () => {
  const response = await fetch(PORTAL + "/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      login: "admin",
      password: "definitely-not-the-password",
      next: "",
    }),
    redirect: "manual",
  });
  assert.equal(response.status, 303);
  const target = assertUsable(response.headers.get("location"), "failed login");
  assert.match(target, /\/login\?error=1$/);
  const followed = await fetch(target);
  assert.equal(followed.status, 200, "the failure redirect target did not serve: " + target);
  assert.ok((await followed.text()).includes("Kredensial tidak valid"));
});

describe("redirect: applying a filter returns to a reachable view", async () => {
  const { cookie } = await login();
  const response = await fetch(PORTAL + "/api/filters", {
    method: "POST",
    headers: { cookie, "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      next: "/t/bct/sales",
      preset: "custom",
      from: "2026-01-01",
      to: "2026-03-31",
    }),
    redirect: "manual",
  });
  assert.equal(response.status, 303);
  const target = assertUsable(response.headers.get("location"), "filters");
  const filterCookie =
    response.headers
      .getSetCookie()
      .find((entry) => entry.startsWith("insight_portal_filters="))
      ?.split(";", 1)[0] ?? "";
  const followed = await fetch(target, { headers: { cookie: cookie + "; " + filterCookie } });
  assert.equal(followed.status, 200, "the filter redirect target did not serve: " + target);
  assert.ok(
    (await followed.text()).includes("2026-01-01"),
    "the filter did not survive its own redirect",
  );
});

describe("redirect: logging out lands on a reachable login page", async () => {
  const { cookie } = await login();
  const response = await fetch(PORTAL + "/api/auth/logout", {
    method: "POST",
    headers: { cookie },
    redirect: "manual",
  });
  assert.equal(response.status, 303);
  const target = assertUsable(response.headers.get("location"), "logout");
  const followed = await fetch(target);
  assert.equal(followed.status, 200, "the logout redirect target did not serve: " + target);
});

describe("redirect: an unauthenticated view request lands on a reachable login page", async () => {
  const response = await fetch(PORTAL + "/t/bct/overview", { redirect: "manual" });
  assert.equal(response.status, 307);
  const target = assertUsable(response.headers.get("location"), "middleware");
  assert.ok(target.includes("/login"));
  const followed = await fetch(target);
  assert.equal(followed.status, 200, "the middleware redirect target did not serve: " + target);
});

describe("redirect: the root sends an authenticated viewer to a reachable dashboard", async () => {
  const { cookie } = await login();
  const response = await fetch(PORTAL + "/", { headers: { cookie }, redirect: "manual" });
  assert.equal(response.status, 307);
  const target = assertUsable(response.headers.get("location"), "root");
  const followed = await fetch(target, { headers: { cookie } });
  assert.equal(followed.status, 200, "the root redirect target did not serve: " + target);
});
