"use client";

/**
 * Chart tokens, read from the CSS custom properties so light and dark are one source of truth and
 * the SVG never carries a hard-coded hex that only suits one mode.
 *
 * Recharts needs concrete colour strings for its SVG attributes, and `var(--x)` works in SVG
 * `stroke`/`fill` in every browser this targets, so the tokens are passed through as `var(...)`
 * rather than resolved in JavaScript. That keeps the dark-mode swap purely in CSS.
 */
export const CHART = {
  series: ["var(--series-1)", "var(--series-2)", "var(--series-3)"],
  grid: "var(--grid)",
  axis: "var(--text-muted)",
  surface: "var(--surface-2)",
  border: "var(--border-strong)",
  text: "var(--text-primary)",
  textMuted: "var(--text-secondary)",
} as const;

/** Second encoding for series identity, so hue is never the only channel. */
export const DASH: ReadonlyArray<string | undefined> = [undefined, "6 4", "2 3"];
export const SHAPES: ReadonlyArray<"circle" | "square" | "triangle"> = [
  "circle",
  "square",
  "triangle",
];
