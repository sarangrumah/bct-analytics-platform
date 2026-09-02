import { notFound } from "next/navigation";

import { getTenant } from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

/**
 * One client, with the buttons custom_super_admin has always had and could
 * never use. Each posts to a route handler that signs to the orchestrator;
 * nothing here talks to the database.
 */
export default async function TenantPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const t = await getTenant(slug);
  if (t === null) notFound();

  const ent = t.entitlement;
  const rows: [string, string][] = [
    ["Database", t.db_name],
    ["Status", t.state],
    ["Paket", t.plan_code ?? "—"],
    ["Berlaku sampai", t.valid_until ?? "tanpa batas"],
    ["Sumber Insight", t.insight_source_kind],
    ["Kontak", t.contact_email ?? "—"],
    ["Dibuat", t.created_at],
    ["Diaktifkan", t.activated_at ?? "—"],
    ["Ditangguhkan", t.suspended_at ?? "—"],
  ];

  return (
    <>
      <h1>{t.display_name}</h1>
      <p className="lede">
        <code>{t.slug}</code> ·{" "}
        <span className={ent?.active ? "pill ok" : "pill bad"}>
          {ent?.active ? "aktif" : "tidak aktif"}
        </span>{" "}
        {(ent?.products ?? []).map((p) => <span key={p} className="pill">{p}</span>)}
      </p>

      <table>
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}><th style={{ width: "14rem" }}>{k}</th><td>{v}</td></tr>
          ))}
        </tbody>
      </table>

      <h2>Tindakan</h2>
      <p>
        {/* Each is a form POST, not a link. A GET that suspends a client is a
            client suspended by a crawler. */}
        <form className="inline" method="POST" action={`/api/tenants/${t.slug}/resume`}>
          <button type="submit">Aktifkan</button>
        </form>{" "}
        <form className="inline" method="POST" action={`/api/tenants/${t.slug}/suspend`}>
          <button type="submit">Tangguhkan</button>
        </form>{" "}
        <form className="inline" method="POST" action={`/api/tenants/${t.slug}/archive`}>
          <button className="danger" type="submit">Arsipkan</button>
        </form>
      </p>
      <p className="lede">
        Perubahan status langsung mengubah klaim <code>subscription_active</code> pada
        login dan penyegaran sesi berikutnya — klien yang ditangguhkan diarahkan ke
        halaman langganan, bukan ke dasbor.
      </p>
    </>
  );
}
