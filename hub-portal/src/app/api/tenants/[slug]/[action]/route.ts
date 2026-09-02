import { NextResponse } from "next/server";

import { absolute } from "@/lib/redirect";
import { call } from "@/lib/orchestrator";
import { getSession } from "@/lib/session";

export const dynamic = "force-dynamic";

/**
 * The three lifecycle buttons, and nothing else.
 *
 * `action` comes out of the URL, so it is matched against a FIXED TABLE rather
 * than interpolated into a path. Without that, `/api/tenants/x/../../whatever`
 * would let the browser choose which orchestrator endpoint this signs for —
 * and it signs with the shared secret, so it would be an oracle for calling
 * anything the orchestrator exposes.
 */
const ACTIONS: Record<string, { method: string; path: (s: string) => string; body: unknown }> = {
  suspend: { method: "POST", path: (s) => `/v1/tenants/${s}/suspend`, body: { reason: "suspended from the console" } },
  resume: { method: "POST", path: (s) => `/v1/tenants/${s}/resume`, body: {} },
  archive: { method: "DELETE", path: (s) => `/v1/tenants/${s}`, body: { retention_days: 30 } },
};

const SLUG_RE = /^[a-z][a-z0-9_]{1,30}$/;

export async function POST(
  request: Request,
  { params }: { params: Promise<{ slug: string; action: string }> },
) {
  const { slug, action } = await params;
  const spec = ACTIONS[action];
  if (!spec || !SLUG_RE.test(slug)) {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }

  // The actor reaches the orchestrator's append-only action log, so "who
  // suspended this client" names a person rather than this service.
  const session = await getSession();
  const actor = session?.sub ?? "hub-portal";

  const { status } = await call(spec.method, spec.path(slug), spec.body, actor);
  const url = await absolute(`/tenants/${slug}`);
  if (status >= 400) url.searchParams.set("error", String(status));
  return NextResponse.redirect(url, { status: 303 });
}
