"use client";

import { CHART, DASH } from "./chart-theme";

export interface SeriesSpec {
  key: string;
  label: string;
}

/**
 * A real HTML legend rather than Recharts' SVG one.
 *
 * It is text in the document, so a screen reader reads it and a text-only view keeps it, and each
 * marker draws the series' dash pattern as well as its hue — the secondary encoding that keeps
 * identity legible without colour.
 */
export function ChartLegend({ series }: { series: SeriesSpec[] }) {
  if (series.length < 2) return null;
  return (
    <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {series.map((entry, index) => (
        <li key={entry.key} className="flex items-center gap-1.5">
          <svg width="22" height="10" aria-hidden="true" focusable="false">
            <line
              x1="1"
              y1="5"
              x2="21"
              y2="5"
              stroke={CHART.series[index % CHART.series.length]}
              strokeWidth="2"
              strokeDasharray={DASH[index % DASH.length]}
            />
          </svg>
          <span>{entry.label}</span>
        </li>
      ))}
    </ul>
  );
}
