import Link from "next/link";

import { VIEWS } from "@/lib/view";

/**
 * The view switcher.
 *
 * The tenant in every link comes from the verified session that the layout resolved, never from
 * the current URL. A viewer therefore cannot navigate themselves into another tenant by editing an
 * address bar and following a link that helpfully preserved the edit.
 *
 * It scrolls horizontally on a phone rather than collapsing into a menu behind a button: five
 * items is not enough to justify hiding four of them behind a tap, and a hamburger would need
 * client JavaScript for a list that fits.
 */
export function Nav({
  tenant,
  active,
  roles,
  subject,
}: {
  tenant: string;
  active: string;
  roles: string[];
  subject: string;
}) {
  return (
    <header
      className="sticky top-0 z-10 border-b"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 sm:px-4">
        <span className="text-sm font-semibold text-ink">BCT Insight</span>
        <span
          className="rounded px-1.5 py-0.5 text-[11px] font-medium"
          style={{ background: "var(--accent-soft)", color: "var(--series-1)" }}
        >
          tenant {tenant}
        </span>
        <span className="hidden text-[11px] text-ink-3 sm:inline">
          {subject} - {roles.length === 0 ? "tanpa peran" : roles.join(", ")}
        </span>
        <form method="post" action="/api/auth/logout" className="ml-auto">
          <button type="submit" className="text-xs underline text-ink-2">
            Keluar
          </button>
        </form>
      </div>
      <nav aria-label="Tampilan" className="mx-auto max-w-6xl px-3 sm:px-4">
        <ul className="-mb-px flex gap-1 overflow-x-auto">
          {VIEWS.map((view) => {
            const current = view.slug === active;
            return (
              <li key={view.slug} className="shrink-0">
                <Link
                  href={"/t/" + tenant + "/" + view.slug}
                  aria-current={current ? "page" : undefined}
                  className="inline-block whitespace-nowrap border-b-2 px-2.5 py-1.5 text-xs sm:text-sm"
                  style={{
                    borderColor: current ? "var(--series-1)" : "transparent",
                    color: current ? "var(--series-1)" : "var(--text-secondary)",
                    fontWeight: current ? 600 : 400,
                  }}
                >
                  {view.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </header>
  );
}
