"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatCompact, formatMeasure } from "@/lib/format";

import { CHART } from "./chart-theme";

export interface CategoryPoint {
  label: string;
  value: number | null;
}

/**
 * Magnitude across nominal categories.
 *
 * Horizontal, because category names are words and a phone is 375px wide: vertical bars force
 * either rotated labels or truncation, and both fail first on the smallest screen. One series is
 * one colour — bars are never ramped by their own value, which would double-encode length as hue.
 *
 * The container height grows with the number of bars instead of compressing them, so the card
 * scrolls with the page rather than growing an inner scrollbar that hides rows.
 */
export function CategoryBarChart({
  data,
  unit,
  type = "decimal",
  title,
  seriesLabel,
}: {
  data: CategoryPoint[];
  unit: string | null;
  type?: string;
  title: string;
  /** What the tooltip and the screen reader call this series. Defaults to the panel title. */
  seriesLabel?: string;
}) {
  const height = Math.max(160, data.length * 30 + 36);
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          accessibilityLayer
          data={data}
          layout="vertical"
          margin={{ top: 4, right: 16, bottom: 4, left: 4 }}
          role="img"
          aria-label={title}
        >
          <CartesianGrid stroke={CHART.grid} strokeWidth={1} horizontal={false} />
          <XAxis
            type="number"
            tick={{ fill: CHART.axis, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: CHART.grid }}
            tickFormatter={(value: number) => formatCompact(value, unit, type)}
          />
          <YAxis
            type="category"
            dataKey="label"
            tick={{ fill: CHART.axis, fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={110}
          />
          <Tooltip
            cursor={{ fill: CHART.grid }}
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
              )
            }
          />
          <Bar
            dataKey="value"
            // Without an explicit name the tooltip and the screen-reader announcement both read
            // the literal key: "value : 99.851 unit". Found by the keyboard audit, which reads the
            // announced text rather than assuming it is sensible.
            name={seriesLabel ?? title}
            fill={CHART.series[0]}
            radius={[0, 4, 4, 0]}
            barSize={16}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
