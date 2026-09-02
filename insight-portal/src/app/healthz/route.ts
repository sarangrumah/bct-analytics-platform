import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Container liveness. No token required and no data exposed - it reports that this process is
 * serving, and nothing about the warehouse, the session or the tenant.
 */
export function GET(): NextResponse {
  return NextResponse.json({ status: "ok", service: "insight-portal" });
}
