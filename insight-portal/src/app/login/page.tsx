import { redirect } from "next/navigation";

import { getSession } from "@/lib/session";

export const dynamic = "force-dynamic";

/**
 * The login form.
 *
 * There is no second authentication system here. The form posts to a route handler on this origin,
 * which forwards the credentials to `login-gateway` over the internal network and keeps the
 * resulting token in an httpOnly cookie. The browser never holds a token it can read, and the
 * credentials never travel to anything except the gateway.
 *
 * Plain HTML, no client JavaScript: a login form that cannot submit without a hydrated bundle is a
 * login form that fails at the worst moment.
 */
export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const session = await getSession();
  if (session !== null) redirect("/t/" + session.tenant_id + "/overview");

  const params = await searchParams;
  const failed = params.error !== undefined;
  const nextRaw = params.next;
  // Only a same-site path is ever accepted as a redirect target: an open redirect on a login page
  // is a credential-phishing primitive.
  const next =
    typeof nextRaw === "string" && nextRaw.startsWith("/") && !nextRaw.startsWith("//")
      ? nextRaw
      : "";

  return (
    <main id="main" className="mx-auto flex min-h-screen max-w-md items-center px-4">
      <div
        className="w-full rounded-lg border p-5"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <h1 className="text-base font-semibold text-ink">BCT Insight Portal</h1>
        <p className="mt-1 text-xs text-ink-3">
          Masuk menggunakan akun Odoo Anda. Sesi diverifikasi terhadap JWKS
          <span className="whitespace-nowrap"> login-gateway</span> dengan algoritma RS256.
        </p>

        {failed ? (
          <p
            role="alert"
            className="mt-3 rounded border px-3 py-2 text-xs"
            style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
          >
            &#9650; Kredensial tidak valid, atau terlalu banyak percobaan. Coba lagi.
          </p>
        ) : null}

        <form method="post" action="/api/auth/login" className="mt-4 space-y-3">
          <input type="hidden" name="next" value={next} />
          <div>
            <label htmlFor="login" className="block text-xs font-medium text-ink-2">
              Pengguna
            </label>
            <input
              id="login"
              name="login"
              type="text"
              autoComplete="username"
              required
              className="mt-1 w-full rounded border px-2 py-1.5 text-sm"
              style={{ borderColor: "var(--border-strong)", background: "var(--surface-2)" }}
            />
          </div>
          <div>
            <label htmlFor="password" className="block text-xs font-medium text-ink-2">
              Kata sandi
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              className="mt-1 w-full rounded border px-2 py-1.5 text-sm"
              style={{ borderColor: "var(--border-strong)", background: "var(--surface-2)" }}
            />
          </div>
          <button
            type="submit"
            className="w-full rounded px-3 py-2 text-sm font-medium text-white"
            style={{ background: "var(--series-1)" }}
          >
            Masuk
          </button>
        </form>
      </div>
    </main>
  );
}
