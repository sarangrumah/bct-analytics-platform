import type { NextConfig } from "next";

const config: NextConfig = {
  // Standalone, so the runtime image carries a server and its own minimal
  // node_modules rather than the whole dependency tree.
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
};

export default config;
