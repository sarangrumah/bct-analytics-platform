import "server-only";

import { cookies } from "next/headers";
import { createRemoteJWKSet, jwtVerify, type JWTPayload } from "jose";

/**
 * The console's session, verified against the gateway's published JWKS.
 *
 * The gate is `is_super_admin`, and NOT `subscription_active`. A super admin
 * is staff, not a client: they have no registry row, so their token carries
 * `subscription_active: false` and an empty `products`. Gating the console on
 * the subscription claim would lock the operators out of the tool they use to
 * fix subscriptions, which is the worst possible moment to be locked out.
 */

export const SESSION_COOKIE = process.env.HUB_PORTAL_SESSION_COOKIE ?? "hub_portal_session";

let jwks: ReturnType<typeof createRemoteJWKSet> | null = null;

function keys() {
  if (!jwks) {
    jwks = createRemoteJWKSet(
      new URL(process.env.HUB_PORTAL_JWKS_URL ?? "http://login-gateway:8080/.well-known/jwks.json"),
    );
  }
  return jwks;
}

export interface Session {
  sub: string;
  tenant_id: string;
  odoo_uid: number;
  is_super_admin: boolean;
  exp: number;
}

export function toSession(p: JWTPayload): Session | null {
  if (typeof p.sub !== "string" || typeof p.tenant_id !== "string") return null;
  return {
    sub: p.sub,
    tenant_id: p.tenant_id,
    odoo_uid: typeof p.odoo_uid === "number" ? p.odoo_uid : -1,
    // `=== true`, never truthiness. An absent or malformed claim must deny.
    is_super_admin: p.is_super_admin === true,
    exp: typeof p.exp === "number" ? p.exp : 0,
  };
}

export async function verify(token: string | undefined): Promise<Session | null> {
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, keys(), {
      issuer: process.env.HUB_PORTAL_JWT_ISSUER ?? "https://login-gateway.local/",
      audience: process.env.HUB_PORTAL_JWT_AUDIENCE ?? "insight-portal",
    });
    return toSession(payload);
  } catch {
    return null;
  }
}

export async function getSession(): Promise<Session | null> {
  const jar = await cookies();
  return verify(jar.get(SESSION_COOKIE)?.value);
}
