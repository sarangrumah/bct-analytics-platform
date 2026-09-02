import { NextResponse, type NextRequest } from "next/server";

import { config } from "@/lib/config";
import { redirectTo } from "@/lib/redirect";
import { verifyToken } from "@/lib/jwt";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Exchange a username and password for a session cookie.
 *
 * This is not a second authentication system. It forwards the credentials to `login-gateway` over
 * the internal network, and the gateway authenticates against Odoo over JSON-RPC and signs the
 * token. This handler holds no key material, mints nothing, and decides nothing about identity.
 *
 * What it does decide is where the token lives: an httpOnly, SameSite=Strict cookie on this
 * origin. The browser can send it back to this application and can do nothing else with it - it
 * cannot be read by script, it is not attached to cross-site requests, and it never reaches the
 * semantic API or the warehouse from the browser, because the browser never talks to either.
 *
 * The token is verified here before the cookie is set. Storing a token this process has not
 * verified would mean the first page render is the first check, and a failure there is far less
 * legible than a failure at the door.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  const form = await request.formData();
  const login = form.get("login");
  const password = form.get("password");
  const nextRaw = form.get("next");
  const next =
    typeof nextRaw === "string" && nextRaw.startsWith("/") && !nextRaw.startsWith("//")
      ? nextRaw
      : null;

  const failure = redirectTo("/login?error=1");

  if (typeof login !== "string" || typeof password !== "string") return failure;

  let upstream: Response;
  try {
    upstream = await fetch(config.loginGatewayUrl + "/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ db: config.odooDatabase, login, password }),
      cache: "no-store",
    });
  } catch {
    return failure;
  }

  if (!upstream.ok) return failure;

  const payload: unknown = await upstream.json().catch(() => null);
  if (typeof payload !== "object" || payload === null) return failure;
  const body = payload as Record<string, unknown>;
  const token = body.access_token;
  const expiresIn = body.expires_in;
  if (typeof token !== "string") return failure;

  const session = await verifyToken(token);
  if (session === null) return failure;

  const destination = next ?? "/t/" + session.tenant_id + "/overview";
  const response = redirectTo(destination);
  response.cookies.set(config.sessionCookieName, token, {
    httpOnly: true,
    secure: config.cookieSecure,
    sameSite: "strict",
    path: "/",
    maxAge: typeof expiresIn === "number" ? expiresIn : 3600,
  });

  /**
   * The gateway's own refresh cookie is scoped to the gateway's origin and path, so the browser
   * would never send it here. It is re-issued as this application's cookie, still httpOnly and
   * still narrow: `/api/auth` is the only path that can spend it. Refresh tokens are opaque and
   * single-use upstream, which is what makes logout mean something.
   */
  const setCookie = upstream.headers.get("set-cookie");
  if (setCookie !== null) {
    const first = setCookie.split(";", 1)[0] ?? "";
    const value = first.slice(first.indexOf("=") + 1);
    if (value !== "") {
      response.cookies.set(config.refreshCookieName, value, {
        httpOnly: true,
        secure: config.cookieSecure,
        sameSite: "strict",
        path: "/api/auth",
      });
    }
  }
  return response;
}
