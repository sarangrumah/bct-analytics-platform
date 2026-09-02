/**
 * What the viewer sees while the panel grid is being fetched.
 *
 * Fixed heights matching the real panels, so the shell does not reflow when the figures land -
 * a dashboard that jumps as each panel arrives is harder to read than one that arrives at once.
 */
export function PanelSkeleton() {
  return (
    <div aria-busy="true" aria-live="polite" className="space-y-3">
      <p className="text-xs text-ink-3">Memuat panel dari lapisan semantik...</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {[0, 1, 2, 3].map((index) => (
          <div
            key={index}
            className="h-48 rounded-lg border"
            style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
          />
        ))}
      </div>
    </div>
  );
}
