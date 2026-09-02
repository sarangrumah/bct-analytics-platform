import type { ReactNode } from "react";

import type { PortalFilters } from "@/lib/filters";
import type { Session } from "@/lib/jwt";

import { FilterBar } from "./FilterBar";
import { Nav } from "./Nav";

/**
 * The frame every view renders inside: navigation, the persistent filter, then the panels.
 *
 * The frame is deliberately cheap to render - it needs no warehouse query - so it can stream to
 * the browser immediately while the panel grid is still being fetched. That is where the perceived
 * load time of this dashboard actually goes: the shell paints, the filter is usable, and the
 * figures arrive into it.
 */
export function ViewShell({
  session,
  active,
  title,
  intro,
  filters,
  ouOptions,
  formNext,
  children,
}: {
  session: Session;
  active: string;
  title: string;
  intro: string;
  filters: PortalFilters;
  ouOptions: number[];
  /** Where the filter form returns to. Defaults to this view; a drill passes its full query. */
  formNext?: string;
  children: ReactNode;
}) {
  const next = formNext ?? "/t/" + session.tenant_id + "/" + active;
  return (
    <div className="min-h-screen">
      <Nav
        tenant={session.tenant_id}
        active={active}
        roles={session.roles}
        subject={session.sub}
      />
      <main id="main" className="mx-auto max-w-6xl px-3 py-4 sm:px-4">
        <h1 className="text-lg font-semibold text-ink sm:text-xl">{title}</h1>
        <p className="mt-1 max-w-3xl text-xs text-ink-2">{intro}</p>
        <div className="mt-3">
          <FilterBar filters={filters} session={session} next={next} ouOptions={ouOptions} />
        </div>
        <div className="mt-4">{children}</div>
        <footer className="mt-8 border-t pt-3 text-[11px] text-ink-3" style={{ borderColor: "var(--border)" }}>
          <p>
            Setiap angka pada halaman ini berasal dari <code>POST /v1/query</code> pada lapisan
            semantik. Portal ini tidak menulis SQL, tidak menghitung ulang metrik, dan tidak memiliki
            kredensial basis data apa pun.
          </p>
          <p className="mt-1">
            Cakupan tenant ditetapkan dari token yang terverifikasi di sisi server. Parameter URL,
            header, cookie, dan kolom formulir tidak dapat mengubahnya.
          </p>
        </footer>
      </main>
    </div>
  );
}
