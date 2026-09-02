/**
 * JWT verification against the gateway's JWKS.
 *
 * Lives apart from `session.ts` because `middleware.ts` needs it and must not import
 * `next/headers`. Everything here is Web-Crypto only, so it runs unchanged in the edge runtime
 * middleware executes in.
 *
 * Verification rules are contract 02 / contract 06 §5, and are pinned rather than negotiated:
 *   - algorithm pinned to RS256, so `alg: none` and HS256 confusion are rejected before a key is
 *     even selected;
 *   - `iss` and `aud` checked exactly;
 *   - `exp`/`nbf` with 30 s leeway;
 *   - key selected by `kid` (two keys are published from day one so rotation needs no outage).
 */
import { createRemoteJWKSet, jwtVerify } from "jose";

import { toSession, type Session } from "./claims";
import { config } from "./config";

let jwks: ReturnType<typeof createRemoteJWKSet> | null = null;

function keySet(): ReturnType<typeof createRemoteJWKSet> {
  if (jwks === null) {
    jwks = createRemoteJWKSet(new URL(config.jwksUrl), {
      cooldownDuration: 30_000,
      cacheMaxAge: 600_000,
    });
  }
  return jwks;
}

/** Verify a bearer token. Returns `null` for every failure — the caller learns nothing else. */
export async function verifyToken(token: string | undefined): Promise<Session | null> {
  if (token === undefined || token === "") return null;
  try {
    const { payload } = await jwtVerify(token, keySet(), {
      algorithms: ["RS256"],
      issuer: config.jwtIssuer,
      audience: config.jwtAudience,
      clockTolerance: 30,
    });
    return toSession(payload);
  } catch {
    return null;
  }
}

export type { Session };
