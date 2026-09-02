import type { QueryFilters } from "./types";

/** A panel's query, in the exact shape contract 06 declares. Shared by the view and the exporter. */
export interface PanelQuery {
  metric: string;
  dimensions: string[];
  filters: QueryFilters;
  order_by?: string;
  limit?: number;
}

/**
 * Encode a panel query for the export route.
 *
 * The encoded blob carries a metric, dimensions, filters, order and limit — the same five fields
 * `/v1/query` takes. It carries no tenant, because there is no tenant field to carry: the export
 * route resolves the tenant from the verified session exactly as the page did. Tampering with this
 * string can change which declared metric is exported; it cannot change whose data comes back.
 */
export function encodePanelQuery(query: PanelQuery): string {
  const json = JSON.stringify(query);
  const bytes = new TextEncoder().encode(json);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function decodePanelQuery(encoded: string): PanelQuery | null {
  try {
    const padded = encoded.replace(/-/g, "+").replace(/_/g, "/");
    const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    const parsed: unknown = JSON.parse(new TextDecoder().decode(bytes));
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
    const candidate = parsed as Record<string, unknown>;
    if (typeof candidate.metric !== "string" || candidate.metric === "") return null;
    if (!Array.isArray(candidate.dimensions)) return null;
    if (candidate.dimensions.some((entry) => typeof entry !== "string")) return null;
    if (
      typeof candidate.filters !== "object" ||
      candidate.filters === null ||
      Array.isArray(candidate.filters)
    ) {
      return null;
    }
    const query: PanelQuery = {
      metric: candidate.metric,
      dimensions: candidate.dimensions as string[],
      filters: candidate.filters as QueryFilters,
    };
    if (typeof candidate.order_by === "string") query.order_by = candidate.order_by;
    if (typeof candidate.limit === "number" && Number.isInteger(candidate.limit)) {
      query.limit = candidate.limit;
    }
    return query;
  } catch {
    return null;
  }
}

export function exportHref(query: PanelQuery, format: "csv" | "xlsx", filename: string): string {
  const params = new URLSearchParams({
    q: encodePanelQuery(query),
    format,
    name: filename,
  });
  return "/api/export?" + params.toString();
}
