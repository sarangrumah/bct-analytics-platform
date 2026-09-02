import { NextResponse } from "next/server";

import { ping } from "@/lib/cms";

export const dynamic = "force-dynamic";

/**
 * Reports what it can actually reach. A health check that only proves the
 * Node process is alive stays green through a database outage, which is the
 * one time anybody reads it.
 */
export async function GET() {
  const cms = await ping();
  return NextResponse.json({ status: cms ? "ok" : "degraded", cms }, { status: cms ? 200 : 503 });
}
