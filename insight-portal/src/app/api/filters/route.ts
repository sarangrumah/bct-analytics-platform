import { NextResponse, type NextRequest } from "next/server";

import { config } from "@/lib/config";
import { redirectTo } from "@/lib/redirect";
import { defaultFilters, isIsoDate, presetRange, serialiseFilters, type PortalFilters } from "@/lib/filters";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Persist the date-range and Operating-Unit filter, then send the viewer back where they were.
 *
 * A cookie rather than client state, so the filter survives navigation between the five views, a
 * hard reload, and a browser with JavaScript switched off - the filter bar is a plain form and this
 * is its target. That is also why the redirect is 303: the back button then returns to the view
 * rather than re-posting the form.
 *
 * The Operating Unit list here NARROWS a query. It cannot widen one: `semantic-api` applies the
 * session's own entitlement predicate on every request, so naming an Operating Unit the session is
 * not entitled to returns nothing rather than granting access to it.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  const form = await request.formData();
  const nextRaw = form.get("next");
  const next =
    typeof nextRaw === "string" && nextRaw.startsWith("/") && !nextRaw.startsWith("//")
      ? nextRaw
      : "/";

  const preset = form.get("preset");
  let filters: PortalFilters;

  if (typeof preset === "string" && preset !== "custom") {
    const days = Number.parseInt(preset, 10);
    filters = Number.isFinite(days) && days > 0 ? presetRange(days) : defaultFilters();
  } else {
    const from = form.get("from");
    const to = form.get("to");
    const base = defaultFilters();
    filters = {
      from: typeof from === "string" && isIsoDate(from) ? from : base.from,
      to: typeof to === "string" && isIsoDate(to) ? to : base.to,
      ou: [],
    };
    if (filters.from > filters.to) filters = base;
  }

  const ou = form
    .getAll("ou")
    .filter((entry): entry is string => typeof entry === "string")
    .map((entry) => Number.parseInt(entry, 10))
    .filter((id) => Number.isInteger(id));
  filters.ou = ou;

  const response = redirectTo(next);
  response.cookies.set(config.filtersCookieName, serialiseFilters(filters), {
    httpOnly: true,
    secure: config.cookieSecure,
    sameSite: "strict",
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
  return response;
}
