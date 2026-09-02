import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "BCT Insight Portal",
  description:
    "Dasbor analitik BCT. Setiap angka berasal dari lapisan semantik, dengan cakupan tenant yang ditetapkan di sisi server.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Never `maximum-scale`: pinch-zoom is how a phone reads a dense table.
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="id">
      <body className="min-h-screen">
        <a className="skip-link" href="#main">
          Lewati ke konten utama
        </a>
        {children}
      </body>
    </html>
  );
}
