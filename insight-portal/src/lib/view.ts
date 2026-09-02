import "server-only";

import { cookies } from "next/headers";

import { config } from "./config";
import { parseFilters, type PortalFilters } from "./filters";

/** The persisted filter for this request. */
export async function loadFilters(): Promise<PortalFilters> {
  const jar = await cookies();
  return parseFilters(jar.get(config.filtersCookieName)?.value);
}

export const VIEWS = [
  { slug: "overview", label: "Ringkasan Eksekutif" },
  { slug: "sales", label: "Penjualan" },
  { slug: "inventory", label: "Persediaan" },
  { slug: "finance", label: "Keuangan" },
  { slug: "ppob", label: "Operasi PPOB" },
] as const;

export type ViewSlug = (typeof VIEWS)[number]["slug"];
