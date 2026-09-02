import { redirect } from "next/navigation";

import { getSession } from "@/lib/session";

export const dynamic = "force-dynamic";

/**
 * The tenant shell.
 *
 * Middleware has already refused a mismatched tenant with 403 before this renders, so the check
 * here is a second, independent one. It is kept deliberately: the two guards fail differently -
 * middleware cannot render a React page and a layout cannot set a status code - and neither is
 * load-bearing for the property that actually matters, which is that `lib/semantic.ts` has no
 * tenant argument at all.
 *
 * If the segment somehow disagrees with the session at this point, the viewer is sent to their own
 * tenant rather than shown anything.
 */
export default async function TenantLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ tenant: string }>;
}) {
  const { tenant } = await params;
  const session = await getSession();
  if (session === null) redirect("/login");
  if (tenant !== session.tenant_id) redirect("/t/" + session.tenant_id + "/overview");
  return <>{children}</>;
}
