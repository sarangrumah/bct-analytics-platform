import { dimensionLabel, formatDimension, formatMeasure } from "@/lib/format";
import type { QueryResponse } from "@/lib/types";

/**
 * The table under every chart.
 *
 * It is not a fallback. It is the text alternative that makes the chart's content available
 * without colour, without shape and without a pointing device, and it is what discharges the
 * data-viz relief rule for the one light-mode series colour that sits under 3:1 against the
 * surface.
 *
 * On a narrow screen a wide table is the worst offender on a dashboard, so this renders as a
 * horizontally scrollable region with an accessible name and a keyboard-reachable scroll
 * container (`tabindex={0}`), rather than shrinking text until it is unreadable or hiding
 * columns the viewer came for.
 */
export function DataTable({
  data,
  caption,
  max = 500,
}: {
  data: QueryResponse;
  caption: string;
  max?: number;
}) {
  const rows = data.rows.slice(0, max);
  const unit = data.meta.unit;
  const signed = data.metric.endsWith("_growth");
  return (
    <div
      tabIndex={0}
      role="region"
      aria-label={caption}
      className="mt-3 max-h-80 overflow-auto rounded border"
      style={{ borderColor: "var(--border)" }}
    >
      <table className="w-full border-collapse text-left text-xs">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0" style={{ background: "var(--surface-2)" }}>
          <tr>
            {data.dimensions.map((dimension) => (
              <th
                key={dimension}
                scope="col"
                className="border-b px-2 py-1.5 font-semibold text-ink-2"
                style={{ borderColor: "var(--border)" }}
              >
                {dimensionLabel(dimension)}
              </th>
            ))}
            <th
              scope="col"
              className="border-b px-2 py-1.5 text-right font-semibold text-ink-2"
              style={{ borderColor: "var(--border)" }}
            >
              {dimensionLabel("value")}
              {unit === null ? "" : ` (${unit})`}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} style={{ background: index % 2 === 1 ? "var(--surface-1)" : undefined }}>
              {data.dimensions.map((dimension) => (
                <td
                  key={dimension}
                  className="whitespace-nowrap px-2 py-1 text-ink-2"
                >
                  {formatDimension(dimension, row[dimension] ?? null)}
                </td>
              ))}
              <td className="tabular whitespace-nowrap px-2 py-1 text-right text-ink">
                {formatMeasure(row.value, data.meta, { signed })}
              </td>
            </tr>
          ))}
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={data.dimensions.length + 1}
                className="px-2 py-3 text-center text-ink-3"
              >
                Tidak ada baris untuk filter ini.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
      {data.rows.length > rows.length ? (
        <p className="px-2 py-1 text-[11px] text-ink-3">
          Menampilkan {rows.length} dari {data.rows.length} baris yang dikembalikan.
        </p>
      ) : null}
    </div>
  );
}
