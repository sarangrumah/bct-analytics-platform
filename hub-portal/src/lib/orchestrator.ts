import "server-only";

import { createHmac } from "node:crypto";

/**
 * The console's only way to change anything, and the signing happens HERE —
 * on the server, in a route handler — never in the browser.
 *
 * The shared secret is the orchestrator's whole authentication boundary. A
 * console that signed in the browser would be a console that ships that secret
 * to every visitor, so this module is `server-only`: importing it from a
 * client component is a build error rather than a leak.
 *
 * The scheme is the one custom_super_admin already uses and the orchestrator
 * already verifies — `t=<ts>,v1=<hex hmac_sha256(secret, ts + "." + body)>`.
 * Three implementations of it now exist (Odoo, this, and the orchestrator's
 * verifier) and they agree byte for byte because none of them invented it.
 */

const BASE = process.env.HUB_PORTAL_ORCHESTRATOR_URL ?? "http://tenant-orchestrator:8080";

function sign(body: string): { header: string } {
  const secret = process.env.ORCHESTRATOR_SHARED_SECRET;
  if (!secret) throw new Error("ORCHESTRATOR_SHARED_SECRET is not set");
  const ts = Math.floor(Date.now() / 1000).toString();
  const mac = createHmac("sha256", secret).update(`${ts}.${body}`).digest("hex");
  return { header: `t=${ts},v1=${mac}` };
}

export interface Tenant {
  id: number;
  slug: string;
  display_name: string;
  db_name: string;
  state: string;
  plan_code: string | null;
  valid_until: string | null;
  insight_source_kind: string;
  contact_email: string | null;
  created_at: string;
  activated_at: string | null;
  suspended_at: string | null;
  entitlement?: { active: boolean; products: string[] };
}

export async function call<T>(
  method: string,
  path: string,
  body?: unknown,
  actor = "hub-portal",
): Promise<{ status: number; data: T | null }> {
  const raw = body === undefined ? "" : JSON.stringify(body);
  const { header } = sign(raw);
  const res = await fetch(BASE + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Custom-Signature": header,
      // Recorded in the append-only action log, so "who suspended this tenant"
      // has an answer that names a person rather than this service.
      "X-Custom-Actor": actor,
    },
    body: raw === "" ? undefined : raw,
    cache: "no-store",
  });
  const text = await res.text();
  let data: T | null = null;
  try {
    data = text ? (JSON.parse(text) as T) : null;
  } catch {
    data = null;
  }
  return { status: res.status, data };
}

export async function listTenants(): Promise<Tenant[]> {
  const { data } = await call<Tenant[]>("GET", "/v1/tenants");
  return data ?? [];
}

export async function getTenant(slug: string): Promise<Tenant | null> {
  const { status, data } = await call<Tenant>("GET", `/v1/tenants/${encodeURIComponent(slug)}`);
  return status === 200 ? data : null;
}

export async function health(): Promise<boolean> {
  try {
    const res = await fetch(BASE + "/healthz", { cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}
