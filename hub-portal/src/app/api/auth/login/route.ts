import { NextResponse } from "next/server";

import { absolute } from "@/lib/redirect";
import { SESSION_COOKIE, verify } from "@/lib/session";

export const dynamic = "force-dynamic";

const GATEWAY = process.env.HUB_PORTAL_LOGIN_GATEWAY_URL ?? "http://login-gateway:8080";
const ADMIN_DB = process.env.ATHERA_ADMIN_DB ?? "athera_admin";

/**
 * Credentials go to the gateway and nowhere else. This handler never sees a
 * password beyond forwarding it, stores nothing, and sets the cookie only
 * after VERIFYING the token it got back — a token accepted on the strength of
 * a 200 would trust the network instead of the signature.
 *
 * The database is fixed to the admin one. A console that let the caller choose
 * a database would let any tenant user reach the login form that mints its
 * session, and the only thing standing between them and the console would then
 * be the is_super_admin claim alone.
 */
export async function POST(request: Request) {
  const form = await request.formData();
  const login = String(form.get("login") ?? "");
  const password = String(form.get("password") ?? "");
  const next = String(form.get("next") ?? "/");

  const res = await fetch(GATEWAY + "/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ db: ADMIN_DB, login, password }),
    cache: "no-store",
  }).catch(() => null);

  const fail = async () =>
    NextResponse.redirect(await absolute("/login?error=1"), { status: 303 });

  if (res === null || !res.ok) return await fail();
  const body = (await res.json().catch(() => null)) as { access_token?: string } | null;
  if (!body?.access_token) return await fail();

  const session = await verify(body.access_token);
  if (session === null || !session.is_super_admin) {
    // A correct password for a non-super-admin lands here. Same response as a
    // wrong password: whether an account exists is not something the login
    // form gets to disclose.
    return await fail();
  }

  // Only a same-site path is ever followed. An open redirect on a login page
  // is a credential-phishing primitive.
  const target = next.startsWith("/") && !next.startsWith("//") ? next : "/";
  const response = NextResponse.redirect(await absolute(target), { status: 303 });
  response.cookies.set(SESSION_COOKIE, body.access_token, {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.HUB_PORTAL_COOKIE_SECURE === "true",
    path: "/",
    maxAge: Math.max(0, session.exp - Math.floor(Date.now() / 1000)),
  });
  return response;
}
