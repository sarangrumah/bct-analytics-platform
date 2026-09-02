"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatCompact, formatMeasure } from "@/lib/format";

import { CHART, DASH } from "./chart-theme";
import { ChartLegend, type SeriesSpec } from "./Legend";

export interface TimePoint {
  /** Already formatted for display by the server; the client never parses a date. */
  label: string;
  [seriesKey: string]: string | number | null;
}

/**
 * Change over time. One axis, always — two measures of different scale get two charts, never two
 * y-scales on one plot.
 *
 * Accessibility:
 *  - `accessibilityLayer` makes the plot focusable and steps through points with the arrow keys,
 *    announcing each one;
 *  - identity is carried by an HTML legend and a dash pattern, not by hue alone;
 *  - the full numbers live in the table the panel renders underneath.
 *
 * Mobile: the container is 224px tall at 375px and 288px from `sm` up, sized to include the x-axis
 * band so the card never grows a nested scrollbar. Tick density is thinned rather than rotated,
 * because rotated tick labels are the first thing that becomes unreadable on a phone.
 */
export function TimeSeriesChart({
  data,
  series,
  unit,
  type = "decimal",
  signed = false,
  title,
}: {
  data: TimePoint[];
  series: SeriesSpec[];
  unit: string | null;
  type?: string;
  signed?: boolean;
  title: string;
}) {
  return (
    <div>
      <ChartLegend series={series} />
      <div className="h-56 w-full sm:h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            accessibilityLayer
            data={data}
            margin={{ top: 8, right: 12, bottom: 4, left: 4 }}
            role="img"
            aria-label={title}
          >
            <CartesianGrid stroke={CHART.grid} strokeWidth={1} vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fill: CHART.axis, fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: CHART.grid }}
              interval="preserveStartEnd"
              minTickGap={24}
            />
            <YAxis
              tick={{ fill: CHART.axis, fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={56}
              tickFormatter={(value: number) => formatCompact(value, unit, type)}
            />
            <Tooltip
              cursor={{ stroke: CHART.border, strokeWidth: 1 }}
              contentStyle={{
                background: CHART.surface,
                border: "1px solid " + CHART.border,
                borderRadius: 6,
                fontSize: 12,
                color: CHART.text,
              }}
              labelStyle={{ color: CHART.textMuted }}
              formatter={(value) =>
                formatMeasure(
                  typeof value === "number" ? value : null,
                  { unit, type },
                  { signed },
                )
              }
            />
            {series.map((entry, index) => (
              <Line
                key={entry.key}
                type="monotone"
                dataKey={entry.key}
                name={entry.label}
                stroke={CHART.series[index % CHART.series.length]}
                strokeWidth={2}
                strokeDasharray={DASH[index % DASH.length]}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 2, stroke: CHART.surface }}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
