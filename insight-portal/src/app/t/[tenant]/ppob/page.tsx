import { Suspense } from "react";
import { redirect } from "next/navigation";

import { FreshnessSummary } from "@/components/Freshness";
import { Kpi, MetricSection } from "@/components/Panel";
import { PanelSkeleton } from "@/components/PanelSkeleton";
import { ViewShell } from "@/components/ViewShell";
import { toQueryFilters, type PortalFilters } from "@/lib/filters";
import { loadOuOptions } from "@/lib/ou";
import type { PanelQuery } from "@/lib/panel";
import { metasOf, runPanels } from "@/lib/panels";
import { getSession } from "@/lib/session";
import { loadFilters } from "@/lib/view";

export const dynamic = "force-dynamic";

/**
 * PPOB operations.
 *
 * The tightest freshness SLA in the platform: 60 seconds, because PPOB is operational and an SLA
 * breach is the point of the view rather than an inconvenience (ADR 0001). A stale badge here means
 * something different from a stale badge on the finance view, which tolerates an hour, and the
 * banner names the SLA so the two are not read the same way.
 *
 * Revenue on this view is `ppob_commission_revenue` and nothing else. The much larger
 * `pass_through_amount` on the same mart is money owed to the biller: measured on live data,
 * binding it would overstate revenue by 481 times, and the registry refuses any metric that sums
 * it as an IDR amount.
 *
 * The success rate is `ppob_success_rate`, a declared ratio metric. It is not computed here from
 * the state breakdown, even though the state breakdown is on the same page.
 */
export default async function PpobPage({ params }: { params: Promise<{ tenant: string }> }) {
  await params;
  const session = await getSession();
  if (session === null) redirect("/login");
  const filters = await loadFilters();
  const ouOptions = await loadOuOptions(session, filters);

  return (
    <ViewShell
      session={session}
      active="ppob"
      title="Operasi PPOB"
      intro="Volume, keberhasilan biller, komisi dan pelanggaran SLA. SLA kesegaran 60 detik - paling ketat di platform ini, karena pelanggaran SLA adalah inti tampilan ini."
      filters={filters}
      ouOptions={ouOptions}
    >
      <Suspense fallback={<PanelSkeleton />}>
        <PpobBody filters={filters} tenant={session.tenant_id} />
      </Suspense>
    </ViewShell>
  );
}

async function PpobBody({ filters, tenant }: { filters: PortalFilters; tenant: string }) {
  const range = toQueryFilters(filters);
  const specs = {
    volume: { metric: "ppob_transaction_count", dimensions: [], filters: range },
    commission: { metric: "ppob_commission_revenue", dimensions: [], filters: range },
    breaches: { metric: "ppob_sla_breach_count", dimensions: [], filters: range },
    successRate: { metric: "ppob_success_rate", dimensions: [], filters: range },
    volumeByMonth: {
      metric: "ppob_transaction_count",
      dimensions: ["date_month"],
      filters: range,
      order_by: "date_month",
    },
    breachesByMonth: {
      metric: "ppob_sla_breach_count",
      dimensions: ["date_month"],
      filters: range,
      order_by: "date_month",
    },
    successByBiller: {
      metric: "ppob_success_rate",
      dimensions: ["biller_code"],
      filters: range,
      order_by: "-value",
    },
    commissionByBiller: {
      metric: "ppob_commission_revenue",
      dimensions: ["biller_code"],
      filters: range,
      order_by: "-value",
    },
    breachesByBiller: {
      metric: "ppob_sla_breach_count",
      dimensions: ["biller_code"],
      filters: range,
      order_by: "-value",
    },
    byState: {
      metric: "ppob_transaction_count",
      dimensions: ["state"],
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

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi label="Jumlah transaksi" result={results.volume} />
        <Kpi
          label="Pendapatan komisi"
          result={results.commission}
          hint="commission_revenue - bukan pass_through_amount"
        />
        <Kpi label="Pelanggaran SLA" result={results.breaches} />
        <Kpi
          label="Tingkat keberhasilan"
          result={results.successRate}
          hint="Metrik ppob_success_rate, dihitung di lapisan semantik"
        />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <MetricSection
          id="ppob-volume"
          title="Transaksi PPOB per bulan"
          result={results.volumeByMonth}
          chart="time"
          query={specs.volumeByMonth}
          filename="ppob-volume-bulanan"
          drillHref={drillBase + "?metric=ppob_transaction_count&by=date_day,biller_code&order=-value&limit=500"}
          drillLabel="Telusuri ke tingkat harian per biller"
        />
        <MetricSection
          id="ppob-breach-month"
          title="Pelanggaran SLA per bulan"
          description="Dipisahkan dari volume dan bukan digambar pada sumbu kedua: dua ukuran dengan skala berbeda pada satu plot mengarang korelasi yang tidak ada dalam data."
          result={results.breachesByMonth}
          chart="time"
          query={specs.breachesByMonth}
          filename="ppob-pelanggaran-bulanan"
        />
        <MetricSection
          id="ppob-success"
          title="Tingkat keberhasilan per biller"
          description="Pembilang adalah transaksi berstatus sukses; penyebutnya seluruh transaksi, sehingga draft, gagal dan dibalik ikut menurunkan angka ini."
          result={results.successByBiller}
          chart="category"
          query={specs.successByBiller}
          filename="ppob-keberhasilan-per-biller"
        />
        <MetricSection
          id="ppob-commission"
          title="Komisi per biller"
          result={results.commissionByBiller}
          chart="category"
          query={specs.commissionByBiller}
          filename="ppob-komisi-per-biller"
          drillHref={drillBase + "?metric=ppob_commission_revenue&by=biller_code,date_month&order=-value&limit=500"}
        />
        <MetricSection
          id="ppob-breach-biller"
          title="Pelanggaran SLA per biller"
          result={results.breachesByBiller}
          chart="category"
          query={specs.breachesByBiller}
          filename="ppob-pelanggaran-per-biller"
          drillHref={drillBase + "?metric=ppob_sla_breach_count&by=biller_code,state&order=-value&limit=500"}
        />
        <MetricSection
          id="ppob-state"
          title="Transaksi per status"
          description="Jumlah baris per status, apa adanya dari lapisan semantik. Rasio keberhasilan ada pada panel tersendiri sebagai metrik yang dideklarasikan."
          result={results.byState}
          chart="category"
          query={specs.byState}
          filename="ppob-per-status"
        />
      </div>
    </div>
  );
}
