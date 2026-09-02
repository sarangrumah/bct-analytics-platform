import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = { title: "ATHERA — Super Admin", robots: "noindex, nofollow" };
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="id">
      <body>
        <header>
          <div className="wrap">
            <Link className="brand" href="/">ATHERA · Super Admin</Link>
            <nav>
              <Link href="/">Klien</Link>
              <Link href="/cms">Konten</Link>
            </nav>
            <a className="login" href="/api/auth/logout">Keluar</a>
          </div>
        </header>
        <main><div className="wrap">{children}</div></main>
      </body>
    </html>
  );
}
