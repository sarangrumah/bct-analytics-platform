import { redirect } from "next/navigation";

import { getSession } from "@/lib/session";

export const dynamic = "force-dynamic";

/**
 * The diagram's "Subscription Info" node.
 *
 * Reached only from middleware, for either of contract 07's two refusals: the subscription is not
 * active, or it is active on a plan that does not include Insight. Both claims come from the
 * control plane — `tenant_registry.is_active()` and `.entitlements()`, one implementation of each
 * rule, consulted on every login and every refresh — so this page is never shown on a hunch about
 * what a plan might be.
 *
 * WHY THIS IS NOT A 403 PAGE. The person is authenticated and is who they say they are; what has
 * run out is the entitlement. A 403 sends people back to a login screen to retype a correct
 * password that cannot help. This page tells them what actually happened and who can fix it.
 *
 * WHY IT NAMES NO PRICE. Billing lives in the control plane and is an operator decision, not a
 * self-service checkout. Sending someone to a payment page this platform does not have would be a
 * worse dead end than the honest one.
 *
 * An active session that arrives here is bounced back to its dashboard rather than shown a
 * confusing "your subscription is fine" page — the same shape as the login page redirecting a
 * session that is already valid.
 */
export default async function SubscriptionPage() {
  const session = await getSession();
  if (session === null) redirect("/login");
  // The bounce condition must be the FULL entitlement, not just `subscription_active`. Checking
  // only the subscription sent a paying client on a non-Insight plan back to the dashboard, which
  // middleware then redirected here again: an infinite loop, and the exact failure this page's
  // `/subscription` exemption exists to prevent.
  const entitled = session.subscription_active && session.products.includes("insight");
  if (entitled) redirect("/t/" + session.tenant_id + "/overview");

  const notEntitled = session.subscription_active;
  const products = session.products.length > 0 ? session.products : null;

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-6 px-6 py-16">
      <div className="rounded-lg border border-neutral-200 bg-white p-6 dark:border-neutral-800 dark:bg-neutral-900">
        <p className="text-xs font-medium uppercase tracking-wide text-amber-700 dark:text-amber-500">
          {notEntitled ? "Paket tidak mencakup Insight" : "Langganan tidak aktif"}
        </p>
        <h1 className="mt-2 text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          {notEntitled
            ? "Paket Anda saat ini tidak mencakup ATHERA Insight"
            : "Akses ke dasbor sedang dihentikan sementara"}
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-neutral-700 dark:text-neutral-300">
          Login Anda berhasil dan identitas Anda dikenali.{" "}
          {notEntitled ? (
            <>
              Langganan tenant{" "}
              <code className="rounded bg-neutral-100 px-1 py-0.5 dark:bg-neutral-800">{session.tenant_id}</code>{" "}
              aktif, tetapi paketnya tidak memuat produk ATHERA Insight. Ini bukan soal kredensial
              maupun masa berlaku.
            </>
          ) : (
            <>
              Yang berakhir adalah masa berlaku langganan untuk tenant{" "}
              <code className="rounded bg-neutral-100 px-1 py-0.5 dark:bg-neutral-800">{session.tenant_id}</code>,
              bukan kredensial Anda. Memasukkan ulang kata sandi tidak akan mengubah apa pun.
            </>
          )}
        </p>

        <dl className="mt-5 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt className="text-neutral-500 dark:text-neutral-400">Tenant</dt>
          <dd className="font-mono text-neutral-900 dark:text-neutral-100">{session.tenant_id}</dd>
          <dt className="text-neutral-500 dark:text-neutral-400">Produk pada paket</dt>
          <dd className="text-neutral-900 dark:text-neutral-100">
            {products ? products.join(", ") : "tidak ada paket aktif"}
          </dd>
        </dl>

        <p className="mt-5 text-sm leading-relaxed text-neutral-700 dark:text-neutral-300">
          Hubungi tim ATHERA untuk mengaktifkan kembali. Setelah langganan diperbarui, halaman ini
          akan otomatis mengembalikan Anda ke dasbor pada login atau penyegaran sesi berikutnya —
          tidak perlu tindakan lain dari sisi Anda.
        </p>

        <div className="mt-6 flex gap-3 text-sm">
          <a
            className="rounded border border-neutral-300 px-3 py-1.5 text-neutral-800 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-200 dark:hover:bg-neutral-800"
            href="/api/auth/logout"
          >
            Keluar
          </a>
        </div>
      </div>

      <p className="text-center text-xs text-neutral-500 dark:text-neutral-400">
        ATHERA Insight
      </p>
    </main>
  );
}
