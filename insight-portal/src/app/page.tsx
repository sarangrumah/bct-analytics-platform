import { redirect } from "next/navigation";

import { getSession } from "@/lib/session";

/**
 * The root sends the viewer to their own tenant.
 *
 * The tenant in the destination URL comes from the verified session, never from anything the
 * request supplied. Middleware has already rejected an unauthenticated request before this runs.
 */
export default async function Home() {
  const session = await getSession();
  if (session === null) redirect("/login");
  redirect("/t/" + session.tenant_id + "/overview");
}
