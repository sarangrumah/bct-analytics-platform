import { NextResponse } from "next/server";

/**
 * Build a redirect whose `Location` a browser can actually reach.
 *
 * There was a real bug here and it shipped. Route handlers used
 * `new URL(destination, request.url)`, and inside the container `request.url` is built from the
 * bind address - `HOSTNAME=0.0.0.0`, correct for binding and meaningless as a destination. A
 * successful login answered:
 *
 *     HTTP/1.1 303 See Other
 *     location: http://0.0.0.0:3000/t/bct/overview
 *
 * The browser resolves `Location` verbatim and fails with ERR_ADDRESS_INVALID. The session cookie
 * was set correctly, so the only broken thing was where the user was sent next - which is why every
 * test passed: the suite carries the cookie and requests views directly, so nothing ever followed
 * the redirect.
 *
 * **`request.nextUrl.clone()` does NOT fix this in a route handler**, which was the first thing
 * tried. In middleware `nextUrl` is populated from the incoming `Host` header, which is why
 * `middleware.ts` was never affected; in a route handler it is derived from `request.url`, so it
 * carries the bind address too. Measured against the running container: after switching all four
 * call sites to `nextUrl.clone()`, the Location was still `http://0.0.0.0:3000/t/bct/overview`.
 *
 * So the `Location` is emitted RELATIVE. RFC 7231 section 7.1.2 allows it and every browser
 * resolves it against the address it actually requested - which is the address the user typed,
 * whatever the container binds to or a proxy rewrites. It also removes any dependence on the `Host`
 * header, so a spoofed `Host` cannot bend a redirect. One idiom, correct in middleware and in route
 * handlers. It takes no request, which is the point: nothing about the incoming request can bend
 * where a user is sent.
 *
 * **This helper is for ROUTE HANDLERS ONLY, and middleware deliberately does not use it.** Trying
 * to unify the two was the second thing attempted and it 500s every request:
 *
 *     TypeError: Invalid URL ... input: '/login?next=%2Ft%2Fbct%2Foverview'
 *
 * Middleware parses its own `Location` with `new URL()`, so a relative one is rejected outright. It
 * must use `NextResponse.redirect(request.nextUrl.clone())`, which is safe there precisely because
 * middleware's `nextUrl` comes from the `Host` header - and Next then serialises the same-origin
 * result back out relative anyway. Two runtimes, two forms, and the asymmetry is real rather than
 * untidy: `nextUrl` means different things in each. Do not merge them.
 */
export function redirectTo(target: string, status: 303 | 307 = 303): NextResponse {
  // Same-site paths only. `//evil.test` is a protocol-relative URL and would be an open redirect,
  // so anything that is not a single-slash path is refused rather than sanitised into something
  // adjacent to what the caller asked for.
  const safe = target.startsWith("/") && !target.startsWith("//") ? target : "/";
  return new NextResponse(null, {
    status,
    headers: { location: safe },
  });
}
