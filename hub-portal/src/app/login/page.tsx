export const dynamic = "force-dynamic";

/**
 * Plain HTML, no client JavaScript. A login form that cannot submit without a
 * hydrated bundle is a login form that fails at the worst moment.
 */
export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const failed = params.error !== undefined;
  const nextRaw = params.next;
  const next = typeof nextRaw === "string" && nextRaw.startsWith("/") && !nextRaw.startsWith("//")
    ? nextRaw
    : "/";

  return (
    <div style={{ maxWidth: "22rem", margin: "3rem auto" }}>
      <h1>Super Admin</h1>
      <p className="lede">Konsol operator ATHERA.</p>
      {failed ? <div className="err">Login gagal, atau akun ini bukan super admin.</div> : null}
      <form method="POST" action="/api/auth/login">
        <input type="hidden" name="next" value={next} />
        <label htmlFor="login">Pengguna</label>
        <input id="login" name="login" autoComplete="username" required />
        <label htmlFor="password">Kata sandi</label>
        <input id="password" name="password" type="password" autoComplete="current-password" required />
        <p><button type="submit">Masuk</button></p>
      </form>
    </div>
  );
}
