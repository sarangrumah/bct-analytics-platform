import { NextResponse } from "next/server";

import { absolute } from "@/lib/redirect";
import { SESSION_COOKIE } from "@/lib/session";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const res = NextResponse.redirect(await absolute("/login"), { status: 303 });
  res.cookies.set(SESSION_COOKIE, "", { httpOnly: true, path: "/", maxAge: 0 });
  return res;
}
