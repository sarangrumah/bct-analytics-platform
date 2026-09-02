import { execFileSync } from "node:child_process";

/**
 * Helpers for the live suite.
 *
 * Everything here is a PRECONDITION helper, and every one of them throws rather than returning a
 * falsy value. A live test that quietly degrades to a no-op when the stack is missing is the exact
 * defect this build keeps catching: a check that returns the right-looking answer for the wrong
 * reason. If the stack is not there, the suite must go red and say which precondition failed.
 */

export const PORTAL = process.env.PORTAL_BASE_URL ?? "http://127.0.0.1:33000";
export const SEMANTIC = process.env.SEMANTIC_API_URL ?? "http://127.0.0.1:38200";
export const GATEWAY = process.env.LOGIN_GATEWAY_URL ?? "http://127.0.0.1:38120";
export const LIVE = process.env.PORTAL_E2E === "1";

export function credentials(): { login: string; password: string } {
  const login = process.env.BCT_DEV_LOGIN ?? "admin";
  const password = process.env.BCT_DEV_PASSWORD;
  if (password === undefined || password === "") {
    throw new Error(
      "BCT_DEV_PASSWORD is not set. The live suite cannot authenticate, and a suite that " +
        "skips authentication proves nothing about a tenant guard.",
    );
  }
  return { login, password };
}

/** Log in through the PORTAL (not the gateway) and return the session cookie header. */
export async function portalSession(): Promise<string> {
  const { login, password } = credentials();
  const body = new URLSearchParams({ login, password, next: "" });
  const response = await fetch(PORTAL + "/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
    redirect: "manual",
  });
  const setCookie = response.headers.getSetCookie();
  const session = setCookie.find((cookie) => cookie.startsWith("insight_portal_session="));
  if (session === undefined) {
    throw new Error(
      "portal login did not set a session cookie (status " +
        response.status +
        ", location " +
        (response.headers.get("location") ?? "none") +
        ")",
    );
  }
  return session.split(";", 1)[0] ?? "";
}

/** A raw gateway token, for talking to the semantic API directly the way a test may need to. */
export async function gatewayToken(db = "bct"): Promise<string> {
  const { login, password } = credentials();
  const response = await fetch(GATEWAY + "/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ db, login, password }),
  });
  if (!response.ok) {
    throw new Error("gateway login failed with " + response.status);
  }
  const payload = (await response.json()) as { access_token?: string };
  if (typeof payload.access_token !== "string") throw new Error("gateway returned no access_token");
  return payload.access_token;
}

/**
 * Count rows a tenant holds in a mart, read straight out of the warehouse.
 *
 * This is the assertion that stops the cross-tenant test from being vacuous. `bct_t2` is a
 * warehouse-only fixture tenant - there is no second Odoo database - so it is entirely possible for
 * it to hold nothing, and a 403 against a tenant with no rows demonstrates only that the tenant has
 * no rows. The row count is therefore part of the test, not something to eyeball beforehand.
 */
export function martRowCount(tenant: string, mart = "mart_revenue_daily"): number {
  const sql =
    "select count(*) from marts." + mart + " where tenant_id = " + "'" + tenant + "'";
  const out = execFileSync(
    "docker",
    ["exec", "odoo19-bct-warehouse-db", "psql", "-U", "warehouse_admin", "-d", "warehouse", "-tAc", sql],
    { encoding: "utf8" },
  );
  const count = Number.parseInt(out.trim(), 10);
  if (!Number.isInteger(count)) {
    throw new Error("could not read a row count for tenant " + tenant + ": " + out);
  }
  return count;
}
