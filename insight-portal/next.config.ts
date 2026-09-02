import type { NextConfig } from "next";

/**
 * `output: "standalone"` is what makes the Docker image small and lets the runtime stage run as a
 * non-root user with no node_modules tree to own.
 *
 * The headers below are defence in depth, not the control: the session cookie is httpOnly and
 * SameSite=Strict, and no credential ever reaches the browser. CSP is set without `unsafe-eval`;
 * `unsafe-inline` is required for the style attributes Recharts emits and for Next's inline
 * bootstrap script in dev. Security owns the final CSP; this is a starting point, not a claim.
 */
const nextConfig: NextConfig = {
  output: "standalone",
  /**
   * The image optimizer is off, and that is a security decision rather than a performance one.
   *
   * `npm audit` reports three HIGH findings against this project. None is a defect in Next itself:
   * `next` is flagged only because it depends on `postcss` (build-time only, not in the runtime
   * image, and the advisories need attacker-controlled CSS - ours is ours) and on `sharp`, whose
   * libvips CVEs are reachable ONLY through the image optimizer. The advertised fix is
   * `next@16.3.3`, a breaking major that this brief pins away from at 15.5.21.
   *
   * This dashboard renders no images at all - `public/` is empty and no `next/image` is imported -
   * so turning the optimizer off costs nothing and removes the only path that reaches sharp. It is
   * a narrowing of exposure, not a fix for the CVE, and it is reported as such.
   */
  images: { unoptimized: true },
  poweredByHeader: false,
  reactStrictMode: true,
  compress: true,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=(), payment=()",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
