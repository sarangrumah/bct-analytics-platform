import Link from "next/link";

import { listTenants } from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

function stateClass(state: string, active: boolean | undefined) {
  if (state === "active" && active) return "pill ok";
  if (state === "suspended" || state === "failed") return "pill bad";
  return "pill warn";
}

/** Client Management — the diagram's node, over the real registry. */
export default async function TenantsPage() {
  const tenants = await listTenants();

  return (
    <>
      <h1>Klien</h1>
      <p className="lede">
        {tenants.length} terdaftar. Status dan hak akses dibaca dari{" "}
        <code>tenant_registry</code> — sumber yang sama dengan yang dikonsultasi
        gerbang login pada setiap sesi.
      </p>
      <table>
        <thead>
          <tr>
            <th>Slug</th><th>Nama</th><th>Status</th><th>Paket</th><th>Sumber Insight</th>
          </tr>
        </thead>
        <tbody>
          {tenants.map((t) => (
            <tr key={t.slug}>
              <td><Link href={`/tenants/${t.slug}`}>{t.slug}</Link></td>
              <td>{t.display_name}</td>
              <td><span className={stateClass(t.state, undefined)}>{t.state}</span></td>
              <td>{t.plan_code ?? "—"}</td>
              <td>{t.insight_source_kind}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {tenants.length === 0 ? (
        <p className="lede">Belum ada klien, atau orchestrator tidak terjangkau.</p>
      ) : null}
    </>
  );
}
