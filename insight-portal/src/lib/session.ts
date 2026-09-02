import "server-only";

import { cookies } from "next/headers";

import { config } from "./config";
import { verifyToken, type Session } from "./jwt";

/**
 * The session, read from an httpOnly cookie and verified on every use.
 *
 * The access token is never sent to the browser as readable content and never embedded in a page.
 * It exists in the cookie jar, which JavaScript cannot read, and in this process.
 */
export async function getSession(): Promise<Session | null> {
  const jar = await cookies();
  return verifyToken(jar.get(config.sessionCookieName)?.value);
}

/** The raw bearer token, for the server-side semantic API client only. Never returned to a page. */
export async function getAccessToken(): Promise<string | null> {
  const jar = await cookies();
  return jar.get(config.sessionCookieName)?.value ?? null;
}

/** Seconds until expiry, from the token's own `exp`. Negative once expired. */
export function secondsUntilExpiry(session: Session): number {
  return session.exp - Math.floor(Date.now() / 1000);
}

export type { Session };
