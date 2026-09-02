import type { Metadata } from "next";
import Link from "next/link";

import { getNav } from "@/lib/cms";

import "./globals.css";

export const metadata: Metadata = {
  title: "ATHERA",
  description: "Platform bisnis: dasbor, ERP, dan asisten AI di atas data Anda sendiri.",
};

// Content comes from the database on every request, so nothing here may be
// statically rendered at build time — the build runs in an image with no
// database, and a page baked then would be a page frozen forever.
export const dynamic = "force-dynamic";

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const nav = await getNav().catch(() => []);
  const loginUrl = process.env.MARKETING_SITE_LOGIN_URL ?? "/login";

  return (
    <html lang="id">
      <body>
        <header>
          <div className="wrap">
            <Link className="brand" href="/">ATHERA</Link>
            <nav>
              {nav.filter((n) => n.slug !== "").map((n) => (
                <Link key={n.slug} href={`/${n.slug}`}>{n.label}</Link>
              ))}
            </nav>
            {/* The diagram's Login node. It leaves this site entirely: the
                gateway owns credentials, and a form here would be a second
                place that could ask for them. */}
            <a className="login" href={loginUrl}>Masuk</a>
          </div>
        </header>
        <main><div className="wrap">{children}</div></main>
        <footer><div className="wrap">© ATHERA</div></footer>
      </body>
    </html>
  );
}
