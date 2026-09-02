import { listPages } from "@/lib/cms";

export const dynamic = "force-dynamic";

/** The CMS half of Super Admin CMS: what the public site says. */
export default async function CmsPage() {
  const pages = await listPages();
  return (
    <>
      <h1>Konten situs</h1>
      <p className="lede">
        Halaman publik di <code>athera.localhost</code>. Menerbitkan atau menarik
        sebuah halaman berlaku seketika — situs membaca view yang hanya berisi
        halaman terbit, jadi draf tidak pernah tersaji.
      </p>
      <table>
        <thead>
          <tr><th>Slug</th><th>Judul</th><th>Blok</th><th>Status</th><th></th></tr>
        </thead>
        <tbody>
          {pages.map((p) => (
            <tr key={p.id}>
              <td><code>/{p.slug}</code></td>
              <td>{p.title}</td>
              <td>{p.blocks}</td>
              <td>
                <span className={p.is_published ? "pill ok" : "pill warn"}>
                  {p.is_published ? "terbit" : "draf"}
                </span>
              </td>
              <td>
                <form className="inline" method="POST" action={`/api/cms/${p.id}`}>
                  <input type="hidden" name="published" value={p.is_published ? "false" : "true"} />
                  <button type="submit">{p.is_published ? "Tarik" : "Terbitkan"}</button>
                </form>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
