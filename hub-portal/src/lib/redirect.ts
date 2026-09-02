import "server-only";

import { headers } from "next/headers";

/**
 * Build an absolute redirect target from the request's own host.
 *
 * NextResponse.redirect insists on an absolute URL, and `new URL(path,
 * request.url)` in a ROUTE HANDLER resolves against the server's BIND address
 * — so a login on http://127.0.0.1:33003 was answered with
 * `Location: http://0.0.0.0:3000/`, which no browser can follow. Measured; the
 * CHANGELOG records the same class of bug taking the portal's login out once
 * already.
 *
 * Middleware does not need this: `nextUrl` there is populated from the
 * incoming Host header. Route handlers are the exception, which is exactly
 * what makes it easy to get wrong twice.
 */
export async function absolute(path: string): Promise<URL> {
  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host") ?? "127.0.0.1:3000";
  const proto = h.get("x-forwarded-proto") ?? "http";
  // Only a same-site path is ever accepted, so a caller cannot turn this into
  // an open redirect by passing an absolute URL.
  const safe = path.startsWith("/") && !path.startsWith("//") ? path : "/";
  return new URL(safe, `${proto}://${host}`);
}
