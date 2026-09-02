import { formatDimension } from "@/lib/format";
import { RANGE_PRESETS, type PortalFilters } from "@/lib/filters";
import type { Session } from "@/lib/jwt";

/**
 * The date-range and Operating-Unit filter.
 *
 * A plain form posting to a route handler that writes a cookie. No client JavaScript, so the
 * filter works on a phone with a slow connection before any bundle has arrived, survives
 * navigation between all five views, and survives a reload.
 *
 * The Operating Unit list is built from the ids the semantic layer actually returned for this
 * session, so it already reflects the session's entitlement - a viewer is never offered an option
 * that would return nothing. It is a narrowing control regardless: `semantic-api` applies the
 * entitlement predicate itself, so ticking a box cannot widen access.
 *
 * The entitlement is stated on screen rather than left implicit, because `allowed_ou: []` means NO
 * Operating Units and `all_ou` is the only bypass. A viewer seeing an empty dashboard deserves to
 * be told that it is their entitlement and not a broken pipeline.
 */
export function FilterBar({
  filters,
  session,
  next,
  ouOptions,
}: {
  filters: PortalFilters;
  session: Session;
  next: string;
  ouOptions: number[];
}) {
  const entitlement = session.all_ou
    ? "Semua Operating Unit (all_ou)"
    : session.allowed_ou.length === 0
      ? "Tanpa Operating Unit - hanya baris UNASSIGNED"
      : "Operating Unit " + session.allowed_ou.join(", ");

  return (
    <form
      method="post"
      action="/api/filters"
      className="rounded-lg border p-3"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <input type="hidden" name="next" value={next} />
      <fieldset className="border-0 p-0">
        <legend className="text-xs font-semibold text-ink">Filter</legend>

        <div className="mt-2 flex flex-wrap items-end gap-2">
          <div>
            <label htmlFor="from" className="block text-[11px] text-ink-2">
              Dari
            </label>
            <input
              id="from"
              name="from"
              type="date"
              defaultValue={filters.from}
              className="mt-0.5 rounded border px-2 py-1 text-xs"
              style={{ borderColor: "var(--border-strong)", background: "var(--surface-2)" }}
            />
          </div>
          <div>
            <label htmlFor="to" className="block text-[11px] text-ink-2">
              Sampai
            </label>
            <input
              id="to"
              name="to"
              type="date"
              defaultValue={filters.to}
              className="mt-0.5 rounded border px-2 py-1 text-xs"
              style={{ borderColor: "var(--border-strong)", background: "var(--surface-2)" }}
            />
          </div>
          <button
            type="submit"
            name="preset"
            value="custom"
            className="rounded px-3 py-1.5 text-xs font-medium text-white"
            style={{ background: "var(--series-1)" }}
          >
            Terapkan
          </button>
          {RANGE_PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="submit"
              name="preset"
              value={String(preset.days)}
              className="rounded border px-2 py-1.5 text-xs text-ink-2"
              style={{ borderColor: "var(--border-strong)", background: "var(--surface-2)" }}
            >
              {preset.label}
            </button>
          ))}
        </div>

        {ouOptions.length === 0 ? null : (
          <details className="mt-2">
            <summary className="cursor-pointer text-[11px] text-ink-2">
              Operating Unit ({filters.ou.length === 0 ? "semua yang berhak" : filters.ou.join(", ")}
              )
            </summary>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
              {ouOptions.map((id) => (
                <label key={id} className="flex items-center gap-1 text-[11px] text-ink-2">
                  <input
                    type="checkbox"
                    name="ou"
                    value={String(id)}
                    defaultChecked={filters.ou.includes(id)}
                  />
                  {formatDimension("operating_unit_id", id)}
                </label>
              ))}
            </div>
          </details>
        )}
      </fieldset>

      <p className="mt-2 text-[11px] text-ink-3">
        Rentang aktif {filters.from} sampai {filters.to}. Hak akses sesi: {entitlement}. Filter ini
        mempersempit kueri dan tidak dapat memperluasnya - cakupan diterapkan di sisi server.
      </p>
    </form>
  );
}
