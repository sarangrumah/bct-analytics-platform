import { Suspense } from "react";
import { redirect } from "next/navigation";

import { FreshnessSummary } from "@/components/Freshness";
import { Card, Kpi, MetricSection, Unavailable } from "@/components/Panel";
import { PanelSkeleton } from "@/components/PanelSkeleton";
import { ViewShell } from "@/components/ViewShell";
import { toQueryFilters, type PortalFilters } from "@/lib/filters";
import { gapsFor } from "@/lib/gaps";
import { loadOuOptions } from "@/lib/ou";
import type { PanelQuery } from "@/lib/panel";
import { metasOf, runPanels } from "@/lib/panels";
import { getSession } from "@/lib/session";
import { loadFilters } from "@/lib/view";

export const dynamic = "force-dynamic";

/**
 * Sales.
 *
 * Per-Operating-Unit and per-product come from declared dimensions. The growth panel is
 * month-over-month and is labelled month-over-month: the warehouse spans 2025-09-01 to 2026-08-31,
 * so no month has a prior-year counterpart and a year-on-year panel would be twelve empty points
 * presented as a chart. That is recorded as an explicit unavailable panel instead.
 *
 * The funnel has no metric. sales_total and sales_untaxed differ only by tax, so drawing them as
 * two funnel stages would invent a conversion that was never measured.
 */
export default async function SalesPage({ params }: { params: Promise<{ tenant: string }> }) {
  await params;
  const session = await getSession();
  if (session === null) redirect("/login");
  const filters = await loadFilters();
  const ouOptions = await loadOuOptions(session, filters);

  return (
    <ViewShell
      session={session}
      active="sales"
      title="Penjualan"
      intro="Nilai pesanan penjualan per bulan, produk, mitra dan Operating Unit, dari mart_sales_daily. Pertumbuhan ditampilkan bulan-ke-bulan."
      filters={filters}
      ouOptions={ouOptions}
    >
      <Suspense fallback={<PanelSkeleton />}>
        <SalesBody filters={filters} tenant={session.tenant_id} />
      </Suspense>
    </ViewShell>
  );
}

async function SalesBody({ filters, tenant }: { filters: PortalFilters; tenant: string }) {
  const range = toQueryFilters(filters);
  const specs = {
    total: { metric: "sales_total", dimensions: [], filters: range },
    untaxed: { metric: "sales_untaxed", dimensions: [], filters: range },
    byMonth: {
      metric: "sales_total",
      dimensions: ["date_month"],
      filters: range,
      order_by: "date_month",
    },
    growth: {
      metric: "revenue_mom_growth",
      dimensions: ["date_month"],
      filters: range,
      order_by: "date_month",
    },
    byProduct: {
      metric: "sales_total",
      dimensions: ["product_key"],
      filters: range,
      order_by: "-value",
      limit: 12,
    },
    byPartner: {
      metric: "sales_total",
      dimensions: ["partner_key"],
      filters: range,
      order_by: "-value",
      limit: 12,
    },
    byOu: {
      metric: "sales_total",
      dimensions: ["operating_unit_id"],
      filters: range,
      order_by: "-value",
    },
  } satisfies Record<string, PanelQuery>;

  const results = await runPanels(specs);
  const { metas } = metasOf(Object.values(results));
  const drillBase = "/t/" + tenant + "/drill";

  return (
    <div className="space-y-4">
      <FreshnessSummary metas={metas} />

      <div className="grid gap-3 sm:grid-cols-2">
        <Kpi label="Total penjualan (termasuk pajak)" result={results.total} />
        <Kpi label="Penjualan sebelum pajak" result={results.untaxed} />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <MetricSection
          id="sales-month"
          title="Penjualan per bulan"
          result={results.byMonth}
          chart="time"
          query={specs.byMonth}
          filename="penjualan-bulanan"
          drillHref={drillBase + "?metric=sales_total&by=date_day,product_key&order=-value&limit=500"}
          drillLabel="Telusuri ke tingkat harian per produk"
        />
        <MetricSection
          id="sales-growth"
          title="Pertumbuhan pendapatan MoM"
          description="revenue_mom_growth, bukan year-on-year. Lihat panel tidak tersedia di bawah untuk alasannya."
          result={results.growth}
          chart="time"
          query={specs.growth}
          filename="pertumbuhan-mom"
        />
        <MetricSection
          id="sales-product"
          title="12 produk teratas"
          description="Diurutkan menurun berdasarkan nilai oleh lapisan semantik (order_by=-value, limit=12), bukan diurutkan di peramban."
          result={results.byProduct}
          chart="category"
          query={specs.byProduct}
          filename="penjualan-per-produk"
          drillHref={drillBase + "?metric=sales_total&by=product_key,date_month&order=-value&limit=500"}
        />
        <MetricSection
          id="sales-partner"
          title="12 mitra teratas"
          description="partner_key sudah dimasker di hulu sesuai kontrak 01; portal tidak pernah melihat nilai aslinya."
          result={results.byPartner}
          chart="category"
          query={specs.byPartner}
          filename="penjualan-per-mitra"
          drillHref={drillBase + "?metric=sales_total&by=partner_key,date_month&order=-value&limit=500"}
        />
        <MetricSection
          id="sales-ou"
          title="Penjualan per Operating Unit"
          result={results.byOu}
          chart="category"
          query={specs.byOu}
          filename="penjualan-per-ou"
          drillHref={drillBase + "?metric=sales_total&by=operating_unit_id,product_key&order=-value"}
        />
      </div>

      <Card
        id="sales-gaps"
        title="Belum tersedia pada tampilan ini"
        subtitle="Diminta oleh brief, tidak dihitung di sini."
      >
        <div className="grid gap-3 sm:grid-cols-2">
          {gapsFor("sales").map((gap) => (
            <Unavailable key={gap.requires} gap={gap} />
          ))}
        </div>
      </Card>
    </div>
  );
}
