import { NextResponse, type NextRequest } from "next/server";

import { config as appConfig } from "@/lib/config";
import { verifyToken } from "@/lib/jwt";
import { TENANT_SCOPE_VIOLATION } from "@/lib/types";

/**
 * The tenant guard, and the only place a 403 status originates.
 *
 * It runs before any page renders, so the refusal cannot depend on a component remembering to
 * check. The rule it enforces is contract 02: a session for tenant A requesting tenant B gets 403
 * with a body that never reveals whether tenant B exists.
 *
 * Note what the `[tenant]` segment in the URL is for, and what it is emphatically not for. It is
 * compared against the verified session and then discarded. It is never forwarded to the semantic
 * API, never stored, and never used to select data: `lib/semantic.ts` takes no tenant argument at
 * all, so there is no parameter through which a URL could change which tenant is queried. The
 * segment exists so that a mis-aimed link fails loudly instead of silently showing the viewer their
 * own data under someone else's name.
 *
 * The response is 403 either way; only the body's media type depends on what the caller asked for.
 * A browser gets a readable page, a test or a script gets the verbatim JSON from contract 02.
 */

const TENANT_ROUTE = /^\/t\/([^/]+)(?:\/|$)/;

function forbidden(request: NextRequest): NextResponse {
  const wantsHtml = (request.headers.get("accept") ?? "").includes("text/html");
  if (!wantsHtml) {
    return NextResponse.json(TENANT_SCOPE_VIOLATION, { status: 403 });
  }
  const html = `<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>403 - Akses ditolak</title>
<style>
:root{color-scheme:light dark}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f4f1;color:#0b0b0b;
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:1.5rem}
main{max-width:34rem;background:#fcfcfb;border:1px solid #e2e1dc;border-radius:.5rem;padding:1.25rem}
h1{margin:0 0 .5rem;font-size:1.05rem}
code{background:#f0efec;border-radius:3px;padding:.1rem .3rem}
a{color:#2a78d6}
@media (prefers-color-scheme:dark){
body{background:#121211;color:#fff}
main{background:#1a1a19;border-color:#35352f}
code{background:#222220}
a{color:#3987e5}}
</style></head><body><main>
<h1>403 &mdash; Sesi ini tidak tercakup pada tenant yang diminta</h1>
<p>Permintaan ditolak sebelum data apa pun dibaca. Tidak ada informasi mengenai tenant yang diminta yang diungkapkan.</p>
<p><code>${TENANT_SCOPE_VIOLATION.error}</code>: ${TENANT_SCOPE_VIOLATION.detail}</p>
<p><a href="/">Kembali ke dasbor Anda</a></p>
</main></body></html>`;
  return new NextResponse(html, {
    status: 403,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

export async function middleware(request: NextRequest): Promise<NextResponse> {
  const { pathname, search } = request.nextUrl;
  const token = request.cookies.get(appConfig.sessionCookieName)?.value;
  const session = await verifyToken(token);

  if (session === null) {
    // Every cause returns the same thing. Distinguishing "expired" from "forged" here would turn
    // the portal into an oracle in exactly the way contract 06 refuses to for the API.
    if (pathname.startsWith("/api/")) {
      return NextResponse.json({ error: "unauthorized", detail: "Invalid token." }, { status: 401 });
    }
    // NOT `redirectTo()`. Middleware parses its own Location with `new URL()`, so a relative one
    // throws ERR_INVALID_URL and the request 500s - measured, after trying exactly that. Here
    // `nextUrl` is populated from the incoming Host header rather than from the bind address, so
    // it is already correct, and Next serialises a same-origin redirect back out relative anyway.
    // See src/lib/redirect.ts for why route handlers cannot do this.
    const login = request.nextUrl.clone();
    login.pathname = "/login";
    login.search = "";
    login.searchParams.set("next", pathname + search);
    return NextResponse.redirect(login);
  }

  const match = TENANT_ROUTE.exec(pathname);
  if (match !== null && match[1] !== session.tenant_id) {
    return forbidden(request);
  }

  /**
   * The diagram's "Active?" decision, enforced here for the same reason the tenant guard is:
   * middleware runs before any page renders, so the refusal cannot depend on a component
   * remembering to check.
   *
   * The subscription page itself is exempt, or an inactive client would be redirected to the
   * page explaining why they were redirected, forever.
   *
   * A LAPSED SUBSCRIPTION IS NOT A 403. The session is valid and the person is who they say they
   * are; what has run out is the entitlement. Treating it as an authorisation failure would send
   * them back to the login screen to re-enter a correct password that cannot help, which is the
   * single most confusing thing this branch could do.
   */
  if (!session.subscription_active && !pathname.startsWith("/subscription")) {
    if (pathname.startsWith("/api/")) {
      return NextResponse.json(
        { error: "subscription_inactive", detail: "This tenant's subscription is not active." },
        { status: 402 },
      );
    }
    const info = request.nextUrl.clone();
    info.pathname = "/subscription";
    info.search = "";
    return NextResponse.redirect(info);
  }

  return NextResponse.next();
}

export const config = {
  /**
   * Everything except the login flow, the health probe, static assets and the Next internals.
   * `/api/auth/login` must stay open or there would be no way to obtain a session.
   */
  matcher: ["/((?!login|api/auth/login|api/auth/logout|healthz|_next/static|_next/image|favicon.ico).*)"],
};
