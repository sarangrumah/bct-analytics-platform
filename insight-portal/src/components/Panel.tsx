import Link from "next/link";
import type { ReactNode } from "react";

import { formatDimension, formatMeasure } from "@/lib/format";
import type { MetricGap } from "@/lib/gaps";
import { exportHref, type PanelQuery } from "@/lib/panel";
import type { QueryResult } from "@/lib/semantic";

import { CategoryBarChart, type CategoryPoint } from "./charts/CategoryBarChart";
import { TimeSeriesChart, type TimePoint } from "./charts/TimeSeriesChart";
import { DataTable } from "./DataTable";
import { Freshness } from "./Freshness";

export function Card({
  title,
  subtitle,
  children,
  id,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  id?: string;
}) {
  return (
    <section
      id={id}
      className="rounded-lg border p-3 sm:p-4"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      aria-labelledby={id === undefined ? undefined : id + "-title"}
    >
      <h3
        id={id === undefined ? undefined : id + "-title"}
        className="text-sm font-semibold text-ink"
      >
        {title}
      </h3>
      {subtitle === undefined ? null : <p className="mt-0.5 text-xs text-ink-3">{subtitle}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

/**
 * The explicit unavailable state.
 *
 * This is a first-class panel, not an omission: it names the metric that would be required and says
 * why the number is not produced here. The alternative - quietly dropping the panel, or filling it
 * with something adjacent - is how a dashboard ends up asserting a figure nobody computed.
 */
export function Unavailable({ gap }: { gap: MetricGap }) {
  const notInBuild = gap.reason === "not_in_build";
  return (
    <section
      className="rounded-lg border border-dashed p-3 sm:p-4"
      style={{ borderColor: "var(--border-strong)", background: "var(--surface-1)" }}
    >
      <div className="flex items-start gap-2">
        <span aria-hidden="true" style={{ color: "var(--status-warning)" }}>
          &#9650;
        </span>
        <div>
          <h3 className="text-sm font-semibold text-ink">{gap.panel}</h3>
          <p className="mt-1 text-xs font-medium" style={{ color: "var(--status-warning)" }}>
            {notInBuild ? "Tidak tersedia pada build ini" : "Belum ada metrik yang dideklarasikan"}
          </p>
          <p className="mt-1 text-xs text-ink-2">{gap.detail}</p>
          <p className="mt-2 text-[11px] text-ink-3">
            {notInBuild ? "Sumber data tidak ada" : "Perlu metrik"}:{" "}
            <code className="rounded px-1" style={{ background: "var(--surface-2)" }}>
              {gap.requires}
            </code>
          </p>
        </div>
      </div>
    </section>
  );
}

export function PanelError({ result }: { result: Extract<QueryResult, { ok: false }> }) {
  return (
    <div
      role="alert"
      className="rounded border p-3 text-xs"
      style={{ borderColor: "var(--status-critical)", background: "var(--surface-2)" }}
    >
      <p className="font-semibold" style={{ color: "var(--status-critical)" }}>
        &#9650; Panel gagal dimuat ({result.status})
      </p>
      <p className="mt-1 text-ink-2">{result.body.detail}</p>
      <p className="mt-1 text-ink-3">
        Kode: <code>{result.body.error}</code>
        {result.body.field === undefined ? null : " - field: " + result.body.field}
      </p>
    </div>
  );
}

/**
 * A single figure. A one-bar bar chart is not a chart, so a headline number is a stat tile.
 */
export function Kpi({
  label,
  result,
  hint,
  signed = false,
}: {
  label: string;
  result: QueryResult;
  hint?: string;
  signed?: boolean;
}) {
  if (!result.ok) {
    return (
      <div
        className="rounded-lg border p-3"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <p className="text-xs text-ink-3">{label}</p>
        <PanelError result={result} />
      </div>
    );
  }
  const first = result.data.rows[0];
  const value = first === undefined ? null : first.value;
  return (
    <div
      className="rounded-lg border p-3"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <p className="text-xs text-ink-2">{label}</p>
      <p className="tabular mt-1 text-xl font-semibold text-ink sm:text-2xl">
        {formatMeasure(value, result.data.meta, { signed })}
      </p>
      {hint === undefined ? null : <p className="mt-0.5 text-[11px] text-ink-3">{hint}</p>}
      <div className="mt-2">
        <Freshness meta={result.data.meta} compact />
      </div>
    </div>
  );
}

export type ChartKind = "time" | "category" | "none";

/**
 * The standard panel: chart, freshness, table, export, and an optional drill-down link.
 *
 * The chart binds to `value` - the measure is always keyed `value` whatever the metric - and the
 * label comes from the panel's first dimension. Nothing here computes: rows are mapped to points
 * and formatted, and that is all.
 */
export function MetricSection({
  id,
  title,
  description,
  result,
  chart,
  query,
  filename,
  drillHref,
  drillLabel,
  seriesLabel,
}: {
  id: string;
  title: string;
  description?: string;
  result: QueryResult;
  chart: ChartKind;
  query: PanelQuery;
  filename: string;
  drillHref?: string;
  drillLabel?: string;
  seriesLabel?: string;
}) {
  if (!result.ok) {
    return (
      <Card id={id} title={title} subtitle={description}>
        <PanelError result={result} />
      </Card>
    );
  }
  const data = result.data;
  const dimension = data.dimensions[0];
  const unit = data.meta.unit;
  const type = data.meta.type;
  const signed = data.metric.endsWith("_growth");
  const label = seriesLabel ?? title;

  let plot: ReactNode = null;
  if (chart === "time" && dimension !== undefined) {
    const points: TimePoint[] = data.rows.map((row) => ({
      label: formatDimension(dimension, row[dimension] ?? null),
      value: row.value,
    }));
    plot = (
      <TimeSeriesChart
        data={points}
        series={[{ key: "value", label }]}
        unit={unit}
        type={type}
        signed={signed}
        title={title}
      />
    );
  } else if (chart === "category" && dimension !== undefined) {
    const points: CategoryPoint[] = data.rows.map((row) => ({
      label: formatDimension(dimension, row[dimension] ?? null),
      value: row.value,
    }));
    plot = (
      <CategoryBarChart
        data={points}
        unit={unit}
        type={type}
        title={title}
        seriesLabel={label}
      />
    );
  }

  return (
    <Card id={id} title={title} subtitle={description}>
      <figure className="m-0">
        {plot}
        <figcaption className="mt-2 text-xs text-ink-2">
          {chartDescription(title, data.rows.length, dimension, unit, type)}
        </figcaption>
      </figure>
      <div className="mt-2">
        <Freshness meta={data.meta} />
      </div>
      <DataTable data={data} caption={"Tabel data untuk " + title} />
      <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <a className="underline" href={exportHref(query, "csv", filename)}>
          Unduh CSV
        </a>
        <a className="underline" href={exportHref(query, "xlsx", filename)}>
          Unduh XLSX
        </a>
        {drillHref === undefined ? null : (
          <Link className="underline" href={drillHref}>
            {drillLabel ?? "Telusuri ke tingkat baris"}
          </Link>
        )}
        <span className="text-ink-3">
          metrik <code>{data.metric}</code> - {data.meta.row_count} baris -{" "}
          {data.meta.query_duration_ms.toFixed(0)} ms
        </span>
      </p>
    </Card>
  );
}

/**
 * The chart's text alternative.
 *
 * Deliberately descriptive rather than interpretive: it says what is plotted, how many points and
 * in what unit, and points at the table. A sentence claiming a trend would be an assertion about
 * the data that nothing in this application is entitled to make.
 */
function chartDescription(
  title: string,
  rows: number,
  dimension: string | undefined,
  unit: string | null,
  type: string,
): string {
  const by = dimension === undefined ? "" : " menurut " + dimension;
  const unitText = unit === null ? (type === "percent" ? " dalam persen" : "") : " dalam " + unit;
  return (
    title +
    by +
    ": " +
    rows +
    " titik data" +
    unitText +
    ". Angka lengkap ada pada tabel di bawah; grafik dapat dijelajahi dengan tombol panah setelah difokuskan."
  );
}
