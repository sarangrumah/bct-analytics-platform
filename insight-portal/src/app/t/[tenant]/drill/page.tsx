import { Suspense } from "react";
import Link from "next/link";
import { redirect } from "next/navigation";

import { Card, MetricSection } from "@/components/Panel";
import { PanelSkeleton } from "@/components/PanelSkeleton";
import { ViewShell } from "@/components/ViewShell";
import { toQueryFilters, type PortalFilters } from "@/lib/filters";
import { loadOuOptions } from "@/lib/ou";
import type { PanelQuery } from "@/lib/panel";
import { catalogue, query } from "@/lib/semantic";
import { getSession } from "@/lib/session";
import { loadFilters } from "@/lib/view";

export const dynamic = "force-dynamic";

/**
 * Drill-down from a summary panel to line level.
 *
 * One route serves every drill, and it is built from `GET /v1/metrics` rather than from a
 * hardcoded list. Contract 06 says to do it that way, and the reason shows up here: adding a
 * dimension upstream makes it drillable with no change to this file, and asking for a dimension
 * the metric does not declare is caught against the catalogue BEFORE a query is sent rather than
 * coming back as a 400 the viewer has to interpret.
 *
 * The parameters carry a metric, dimensions, an order and a limit. They do not carry a tenant, and
 * `query()` has no argument through which one could be supplied.
 */
export default async function DrillPage({
  params,
  searchParams,
}: {
  params: Promise<{ tenant: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  await params;
  const session = await getSession();
  if (session === null) redirect("/login");
  const filters = await loadFilters();
  const ouOptions = await loadOuOptions(session, filters);
  const search = await searchParams;

  const metric = typeof search.metric === "string" ? search.metric : "";
  const by = typeof search.by === "string" ? search.by.split(",").filter(Boolean) : [];
  const order = typeof search.order === "string" ? search.order : "-value";
  const limitRaw = typeof search.limit === "string" ? Number.parseInt(search.limit, 10) : 500;
  const limit = Number.isInteger(limitRaw) && limitRaw > 0 ? Math.min(limitRaw, 5000) : 500;

  // Applying a filter from a drill must return to the same drill, not to a bare /drill with no
  // metric. Only the parameters this route understands are carried back.
  const back = new URLSearchParams({ metric, by: by.join(","), order, limit: String(limit) });
  const formNext = "/t/" + session.tenant_id + "/drill?" + back.toString();

  return (
    <ViewShell
      session={session}
      active="drill"
      title="Telusuri detail"
      intro="Baris tingkat detail untuk metrik yang dipilih, dengan filter yang sama seperti tampilan asalnya."
      filters={filters}
      ouOptions={ouOptions}
      formNext={formNext}
    >
      <Suspense fallback={<PanelSkeleton />}>
        <DrillBody
          metric={metric}
          by={by}
          order={order}
          limit={limit}
          filters={filters}
          tenant={session.tenant_id}
        />
      </Suspense>
    </ViewShell>
  );
}

async function DrillBody({
  metric,
  by,
  order,
  limit,
  filters,
  tenant,
}: {
  metric: string;
  by: string[];
  order: string;
  limit: number;
  filters: PortalFilters;
  tenant: string;
}) {
  const cat = await catalogue();
  if (cat === null) {
    return (
      <Card title="Katalog metrik tidak dapat dibaca">
        <p className="text-xs text-ink-2">
          Lapisan semantik tidak mengembalikan katalog, sehingga permintaan ini tidak divalidasi dan
          tidak dikirim.
        </p>
      </Card>
    );
  }

  const definition = cat.metrics.find((entry) => entry.name === metric);
  if (definition === undefined) {
    return (
      <Card title="Metrik tidak dikenal">
        <p className="text-xs text-ink-2">
          <code>{metric}</code> tidak ada dalam katalog. Tidak ada kueri yang dikirim.
        </p>
        <p className="mt-2 text-xs text-ink-3">
          Metrik yang tersedia: {cat.metrics.map((entry) => entry.name).join(", ")}
        </p>
      </Card>
    );
  }

  const unknown = by.filter((dimension) => !definition.dimensions.includes(dimension));
  if (unknown.length > 0) {
    return (
      <Card title="Dimensi tidak dideklarasikan">
        <p className="text-xs text-ink-2">
          <code>{unknown.join(", ")}</code> tidak dideklarasikan untuk metrik{" "}
          <code>{definition.name}</code>. Ditolak sebelum kueri dikirim.
        </p>
        <p className="mt-2 text-xs text-ink-3">
          Dimensi yang dideklarasikan: {definition.dimensions.join(", ")}
        </p>
      </Card>
    );
  }

  const declaresDateRange = Object.prototype.hasOwnProperty.call(definition.filters, "date_range");
  const declaresOu = Object.prototype.hasOwnProperty.call(definition.filters, "operating_unit_id");
  const spec: PanelQuery = {
    metric: definition.name,
    dimensions: by,
    filters: toQueryFilters(filters, {
      dateRange: declaresDateRange,
      operatingUnit: declaresOu,
    }),
    order_by: order,
    limit,
  };

  const result = await query(spec);

  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-3">
        <Link className="underline" href={"/t/" + tenant + "/overview"}>
          Kembali ke ringkasan
        </Link>
      </p>
      <MetricSection
        id="drill"
        title={definition.label}
        description={definition.description}
        result={result}
        chart={by[0] === "date_day" || by[0] === "date_month" ? "time" : "category"}
        query={spec}
        filename={"drill-" + definition.name}
      />
    </div>
  );
}
