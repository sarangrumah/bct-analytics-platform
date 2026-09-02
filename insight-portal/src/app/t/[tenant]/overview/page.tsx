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
 * Executive overview.
 *
 * Revenue is `revenue_net`, which UNIONs three channels - invoice, POS and PPOB commission - and
 * sums across them deliberately, as the metric's own `channel_note` declares. The channel
 * breakdown is on the page so the total is never read as one line of business. PPOB's contribution
 * is commission only; `pass_through_amount` is money owed to the biller, is not revenue, and no
 * metric in the registry will sum it.
 *
 * Margin, receivables ageing and cash position have no declared metric and are rendered as
 * explicit unavailable panels naming what each would need.
 */
export default async function OverviewPage({
  params,
}: {
  params: Promise<{ tenant: string }>;
}) {
  await params;
  const session = await getSession();
  if (session === null) redirect("/login");
  const filters = await loadFilters();
  const ouOptions = await loadOuOptions(session, filters);

  return (
    <ViewShell
      session={session}
      active="overview"
      title="Ringkasan Eksekutif"
      intro="Pendapatan neto lintas tiga kanal, pertumbuhan bulanan, dan sebaran per Operating Unit. Setiap panel menyertakan waktu pembaruan pipeline dan SLA kesegarannya sendiri."
      filters={filters}
      ouOptions={ouOptions}
    >
      <Suspense fallback={<PanelSkeleton />}>
        <OverviewBody filters={filters} tenant={session.tenant_id} />
      </Suspense>
    </ViewShell>
  );
}

async function OverviewBody({
  filters,
  tenant,
}: {
  filters: PortalFilters;
  tenant: string;
}) {
  const range = toQueryFilters(filters);
  const specs = {
    totalRevenue: { metric: "revenue_net", dimensions: [], filters: range },
    totalSales: { metric: "sales_total", dimensions: [], filters: range },
    ppobCommission: { metric: "ppob_commission_revenue", dimensions: [], filters: range },
    successRate: { metric: "ppob_success_rate", dimensions: [], filters: range },
    revenueByMonth: {
      metric: "revenue_net",
      dimensions: ["date_month"],
      filters: range,
      order_by: "date_month",
    },
    growthByMonth: {
      metric: "revenue_mom_growth",
      dimensions: ["date_month"],
      filters: range,
      order_by: "date_month",
    },
    revenueByChannel: {
      metric: "revenue_net",
      dimensions: ["revenue_channel"],
      filters: range,
      order_by: "-value",
    },
    revenueByOu: {
      metric: "revenue_net",
      dimensions: ["operating_unit_id"],
      filters: range,
      order_by: "-value",
    },
  } satisfies Record<string, PanelQuery>;

  const results = await runPanels(specs);
  const { metas } = metasOf(Object.values(results));
  const gaps = gapsFor("overview");
  const drillBase = "/t/" + tenant + "/drill";

  return (
    <div className="space-y-4">
      <FreshnessSummary metas={metas} />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi label="Pendapatan neto (3 kanal)" result={results.totalRevenue} />
        <Kpi label="Total penjualan" result={results.totalSales} />
        <Kpi
          label="Komisi PPOB"
          result={results.ppobCommission}
          hint="Komisi saja - pass-through milik biller bukan pendapatan"
        />
        <Kpi label="Keberhasilan PPOB" result={results.successRate} />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <MetricSection
          id="revenue-month"
          title="Pendapatan neto per bulan"
          description="revenue_net, dijumlahkan lintas kanal invoice, pos dan ppob_commission."
          result={results.revenueByMonth}
          chart="time"
          query={specs.revenueByMonth}
          filename="pendapatan-neto-bulanan"
          drillHref={
            drillBase +
            "?metric=revenue_net&by=date_day,revenue_channel&order=-value&limit=500"
          }
          drillLabel="Telusuri ke tingkat harian"
        />
        <MetricSection
          id="revenue-growth"
          title="Pertumbuhan pendapatan bulan-ke-bulan (MoM)"
          description="revenue_mom_growth. Bulan pertama pada rentang tidak memiliki pembanding sehingga bernilai kosong, bukan nol. Ini MoM, bukan YoY."
          result={results.growthByMonth}
          chart="time"
          query={specs.growthByMonth}
          filename="pertumbuhan-mom"
        />
        <MetricSection
          id="revenue-channel"
          title="Pendapatan neto per kanal"
          description="Dimensi revenue_channel dari mart_revenue_daily."
          result={results.revenueByChannel}
          chart="category"
          query={specs.revenueByChannel}
          filename="pendapatan-per-kanal"
          drillHref={drillBase + "?metric=revenue_net&by=revenue_channel,date_month&order=-value"}
        />
        <MetricSection
          id="revenue-ou"
          title="Pendapatan neto per Operating Unit"
          description="operating_unit_id -1 adalah anggota UNASSIGNED yang eksplisit, bukan nilai kosong."
          result={results.revenueByOu}
          chart="category"
          query={specs.revenueByOu}
          filename="pendapatan-per-ou"
          drillHref={drillBase + "?metric=revenue_net&by=operating_unit_id,product_key&order=-value"}
        />
      </div>

      <Card
        id="overview-gaps"
        title="Belum tersedia pada tampilan ini"
        subtitle="Panel berikut diminta oleh brief tetapi tidak memiliki metrik yang dideklarasikan. Tidak ada satu pun yang dihitung di sini."
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {gaps.map((gap) => (
            <Unavailable key={gap.requires} gap={gap} />
          ))}
        </div>
      </Card>
    </div>
  );
}
