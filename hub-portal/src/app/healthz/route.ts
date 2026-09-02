import { NextResponse } from "next/server";

import { health } from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

/** Reports whether the orchestrator is reachable, not merely that Node is up. */
export async function GET() {
  const ok = await health();
  return NextResponse.json({ status: ok ? "ok" : "degraded", orchestrator: ok }, { status: ok ? 200 : 503 });
}
