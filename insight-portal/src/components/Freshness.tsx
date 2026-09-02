import { formatRefreshedAt, formatSla } from "@/lib/format";
import type { QueryMeta } from "@/lib/types";

/**
 * "Last refreshed at", sourced from `meta.last_refreshed_at`.
 *
 * Everything shown here came out of the API response, which read it from
 * `warehouse.mart_freshness` over `warehouse.pipeline_state`. Nothing is derived from any clock:
 * there is no "x minutes ago" anywhere in this application, because that would be the viewer's
 * device doing arithmetic on a pipeline fact. Staleness is `meta.is_stale` — the warehouse's
 * verdict, not ours — and a mart with no pipeline_state row reports stale, because unknown
 * freshness is not fresh freshness.
 *
 * The SLA is shown alongside because it is not uniform: PPOB is 60 seconds and finance is 60
 * minutes (ADR 0001), so "13 minutes old" is a page for one view and unremarkable for another.
 * State is carried by an icon and a word as well as by colour.
 */
export function Freshness({ meta, compact = false }: { meta: QueryMeta; compact?: boolean }) {
  const stale = meta.is_stale;
  return (
    <p
      className={
        "flex flex-wrap items-center gap-x-2 gap-y-1 " +
        (compact ? "text-[11px]" : "text-xs") +
        " text-ink-3"
      }
    >
      <span
        className="inline-flex items-center gap-1 font-medium"
        style={{ color: stale ? "var(--status-critical)" : "var(--status-good)" }}
      >
        <span aria-hidden="true">{stale ? "▲" : "●"}</span>
        <span>{stale ? "Basi" : "Segar"}</span>
      </span>
      {/*
        Each of these is ONE interpolated string rather than a literal next to an expression.
        React separates adjacent text nodes with an HTML comment, so `SLA {value}` renders as
        `SLA <!-- -->60 detik` - which reads correctly on screen but breaks find-in-page, breaks
        copy-paste of the timestamp, and is not the contiguous text a screen reader announces.
      */}
      <span className="text-ink-3">{"Diperbarui " + formatRefreshedAt(meta.last_refreshed_at)}</span>
      <span className="text-ink-3">{"· SLA " + formatSla(meta.refresh_sla_seconds)}</span>
      <span className="text-ink-3">{"· " + meta.source_model}</span>
      {meta.note === undefined ? null : (
        <span style={{ color: "var(--status-warning)" }}>{"· " + meta.note}</span>
      )}
    </p>
  );
}

/**
 * The view-level banner. Takes every panel's `meta` and reports the worst case, so a viewer is
 * told the view is stale even when only one of its panels is.
 */
export function FreshnessSummary({ metas }: { metas: QueryMeta[] }) {
  if (metas.length === 0) return null;
  const stale = metas.filter((meta) => meta.is_stale);
  const tightest = metas.reduce((acc, meta) =>
    meta.refresh_sla_seconds < acc.refresh_sla_seconds ? meta : acc,
  );
  const oldest = metas.reduce((acc, meta) => {
    if (acc.last_refreshed_at === null) return acc;
    if (meta.last_refreshed_at === null) return meta;
    return meta.last_refreshed_at < acc.last_refreshed_at ? meta : acc;
  });
  const isStale = stale.length > 0;
  return (
    <div
      role="status"
      className="rounded-md border px-3 py-2 text-xs"
      style={{
        borderColor: isStale ? "var(--status-critical)" : "var(--border)",
        background: "var(--surface-2)",
      }}
    >
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span
          className="inline-flex items-center gap-1 font-semibold"
          style={{ color: isStale ? "var(--status-critical)" : "var(--status-good)" }}
        >
          <span aria-hidden="true">{isStale ? "▲" : "●"}</span>
          {isStale ? `${stale.length} dari ${metas.length} panel basi` : "Semua panel segar"}
        </span>
        <span className="text-ink-2">
          {"Data tertua dalam tampilan ini: " + formatRefreshedAt(oldest.last_refreshed_at)}
        </span>
        <span className="text-ink-3">
          {"· SLA paling ketat di sini: " +
            formatSla(tightest.refresh_sla_seconds) +
            " (" +
            tightest.source_model +
            ")"}
        </span>
      </p>
      <p className="mt-1 text-ink-3">
        Waktu di atas berasal dari metadata pipeline (<code>meta.last_refreshed_at</code>), bukan
        dari jam perangkat Anda.
      </p>
    </div>
  );
}
