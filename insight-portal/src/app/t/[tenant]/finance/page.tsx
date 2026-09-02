import { Suspense } from "react";
import { redirect } from "next/navigation";

import { FreshnessSummary } from "@/components/Freshness";
import { Card, MetricSection, Unavailable } from "@/components/Panel";
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
 * Finance.
 *
 * The general-ledger figures come from `account_balance` over `fct_account_move_line` - a signed
 * debit-minus-credit balance per account, on a 60-minute freshness SLA. Financial reporting
 * tolerates hourly (ADR 0001), and the panels say so rather than implying the numbers are live.
 *
 * Two things this view deliberately does NOT do:
 *
 *  1. It does not invent two metric names for the split. `account_type` and `is_profit_and_loss`
 *     are dimensions of `account_balance`, so the profit-and-loss and balance-sheet views are a
 *     group-by on one measure. Two metric names over the same measure filtered differently would
 *     be a view wearing a metric's clothes, and Backend declined to declare them for that reason.
 *  2. It does not show a PPN/PPh summary. The operator chose a four-addon set, so there are no
 *     Coretax/e-Faktur or PPh withholding modules and no tax data exists in this build. The panel
 *     states that. It is not computed, not estimated, and not derived from anything nearby.
 */
export default async function FinancePage({ params }: { params: Promise<{ tenant: string }> }) {
  await params;
  const session = await getSession();
  if (session === null) redirect("/login");
  const filters = await loadFilters();
  const ouOptions = await loadOuOptions(session, filters);

  return (
    <ViewShell
      session={session}
      active="finance"
      title="Keuangan"
      intro="Saldo buku besar dari fct_account_move_line: debit dikurangi kredit pada baris jurnal yang sudah diposting, dipecah menurut jenis akun. SLA kesegaran 60 menit, paling longgar di platform ini."
      filters={filters}
      ouOptions={ouOptions}
    >
      <Suspense fallback={<PanelSkeleton />}>
        <FinanceBody filters={filters} tenant={session.tenant_id} />
      </Suspense>
    </ViewShell>
  );
}

async function FinanceBody({ filters, tenant }: { filters: PortalFilters; tenant: string }) {
  const range = toQueryFilters(filters);
  const specs = {
    byType: {
      metric: "account_balance",
      dimensions: ["account_type"],
      filters: range,
      order_by: "-value",
    },
    bySplit: {
      metric: "account_balance",
      dimensions: ["is_profit_and_loss", "account_type"],
      filters: range,
      order_by: "-value",
    },
    byAccount: {
      metric: "account_balance",
      dimensions: ["account_id"],
      filters: range,
      order_by: "-value",
      limit: 30,
    },
    byMonth: {
      metric: "account_balance",
      dimensions: ["date_month"],
      filters: range,
      order_by: "date_month",
    },
    byRevenueLine: {
      metric: "account_balance",
      dimensions: ["is_revenue_line"],
      filters: range,
      order_by: "-value",
    },
    byMoveType: {
      metric: "account_balance",
      dimensions: ["move_type"],
      filters: range,
      order_by: "-value",
    },
    byPaymentState: {
      metric: "account_balance",
      dimensions: ["payment_state"],
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

      <div className="grid gap-3 lg:grid-cols-2">
        <MetricSection
          id="gl-account-type"
          title="Saldo per jenis akun"
          description="Dimensi account_type. Inilah pemisahan laba rugi dan neraca: sebuah group-by pada account_balance, bukan dua metrik dengan nama berbeda untuk ukuran yang sama."
          result={results.byType}
          chart="category"
          query={specs.byType}
          filename="saldo-per-jenis-akun"
          drillHref={drillBase + "?metric=account_balance&by=account_type,account_id&order=-value&limit=500"}
        />
        <MetricSection
          id="gl-split"
          title="Laba rugi dan neraca"
          description="is_profit_and_loss dapat bernilai NULL: baris seksi dan catatan tidak memiliki akun, dan NULL berarti bukan keduanya - bukan false. Seed ini kebetulan tidak memuat baris seperti itu, dan itu bukan bukti bahwa baris seperti itu tidak mungkin ada, sehingga kelompoknya tetap dirender."
          result={results.bySplit}
          chart="category"
          query={specs.bySplit}
          filename="laba-rugi-dan-neraca"
          drillHref={drillBase + "?metric=account_balance&by=is_profit_and_loss,account_type,account_id&order=-value&limit=500"}
        />
        <MetricSection
          id="gl-account"
          title="Saldo per akun"
          description="Debit dikurangi kredit per account_id. Tanda positif berarti saldo debit."
          result={results.byAccount}
          chart="category"
          query={specs.byAccount}
          filename="saldo-per-akun"
          drillHref={drillBase + "?metric=account_balance&by=account_id,date_month&order=-value&limit=500"}
        />
        <MetricSection
          id="gl-month"
          title="Pergerakan buku besar per bulan"
          result={results.byMonth}
          chart="time"
          query={specs.byMonth}
          filename="buku-besar-bulanan"
          drillHref={drillBase + "?metric=account_balance&by=date_day,account_id&order=-value&limit=500"}
        />
        <MetricSection
          id="gl-revenue-line"
          title="Baris pendapatan dan bukan pendapatan"
          description="Dimensi is_revenue_line dari mart. Ini adalah pembeda terdekat yang ada terhadap pemisahan laba rugi, dan bukan penggantinya."
          result={results.byRevenueLine}
          chart="category"
          query={specs.byRevenueLine}
          filename="baris-pendapatan"
        />
        <MetricSection
          id="gl-move-type"
          title="Saldo per jenis jurnal"
          result={results.byMoveType}
          chart="category"
          query={specs.byMoveType}
          filename="saldo-per-jenis-jurnal"
          drillHref={drillBase + "?metric=account_balance&by=move_type,partner_key&order=-value&limit=500"}
        />
        <MetricSection
          id="gl-payment-state"
          title="Saldo per status pembayaran"
          description="payment_state menunjukkan status pelunasan, bukan umur piutang: tanggal jatuh tempo tidak ada pada mart ini."
          result={results.byPaymentState}
          chart="category"
          query={specs.byPaymentState}
          filename="saldo-per-status-pembayaran"
        />
      </div>

      <Card
        id="finance-gaps"
        title="Kejujuran cakupan tampilan Keuangan"
        subtitle="Dua hal berikut sengaja tidak ditampilkan, dan alasannya berbeda."
      >
        <div className="grid gap-3 lg:grid-cols-2">
          {gapsFor("finance").map((gap) => (
            <Unavailable key={gap.requires} gap={gap} />
          ))}
        </div>
      </Card>
    </div>
  );
}
