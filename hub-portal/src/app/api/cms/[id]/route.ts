import { NextResponse } from "next/server";

import { absolute } from "@/lib/redirect";
import { setPublished } from "@/lib/cms";
import { getSession } from "@/lib/session";

export const dynamic = "force-dynamic";

export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const pageId = Number(id);
  if (!Number.isInteger(pageId) || pageId <= 0) {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }
  const form = await request.formData();
  // Explicit "true", not truthiness: a missing or malformed field must not
  // publish a page by accident.
  const published = String(form.get("published") ?? "") === "true";
  const session = await getSession();
  await setPublished(pageId, published, session?.sub ?? "hub-portal");
  return NextResponse.redirect(await absolute("/cms"), { status: 303 });
}
