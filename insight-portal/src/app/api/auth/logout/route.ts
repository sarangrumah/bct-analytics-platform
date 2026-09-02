import { NextResponse, type NextRequest } from "next/server";

import { cacheClear } from "@/lib/cache";
import { config } from "@/lib/config";
import { redirectTo } from "@/lib/redirect";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Drop the session.
 *
 * The upstream refresh token is revoked at the gateway as well as forgotten here, because a
 * refresh token that survives logout makes logout a lie - which is exactly why contract 06 has
 * them opaque and single-use rather than self-contained JWTs.
 *
 * The server-side aggregate cache is cleared too. It is keyed on the session, so nothing could
 * leak across users, but a machine that has been logged out should not be holding the previous
 * user's figures in memory either.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  const refresh = request.cookies.get(config.refreshCookieName)?.value;
  if (refresh !== undefined) {
    try {
      await fetch(config.loginGatewayUrl + "/auth/logout", {
        method: "POST",
        headers: { cookie: config.gatewayRefreshCookieName + "=" + refresh },
        cache: "no-store",
      });
    } catch {
      // A gateway that cannot be reached must not keep the user logged in locally.
    }
  }
  cacheClear();
  const response = redirectTo("/login");
  response.cookies.delete(config.sessionCookieName);
  response.cookies.delete(config.refreshCookieName);
  return response;
}
