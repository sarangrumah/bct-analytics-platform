import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE, verify } from "@/lib/session";

/**
 * The console's only gate, and it runs before any page renders so a refusal
 * cannot depend on a component remembering to check.
 *
 * TWO REFUSALS, DELIBERATELY DIFFERENT. No session at all is a redirect to the
 * login form — the person may simply not have signed in. A VALID session
 * without `is_super_admin` is a 403 that does not redirect: sending a
 * legitimate tenant user to a login page would invite them to try again with
 * the same correct password forever.
 */
export async function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const session = await verify(request.cookies.get(SESSION_COOKIE)?.value);

  if (session === null) {
    if (pathname.startsWith("/api/")) {
      return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    }
    const login = request.nextUrl.clone();
    login.pathname = "/login";
    login.search = "";
    login.searchParams.set("next", pathname + search);
    return NextResponse.redirect(login);
  }

  if (!session.is_super_admin) {
    return NextResponse.json(
      {
        error: "forbidden",
        detail: "This console is for ATHERA operators. Your session is valid but not a super admin.",
      },
      { status: 403 },
    );
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!login|api/auth/login|api/auth/logout|healthz|_next/static|_next/image|favicon.ico).*)"],
};
