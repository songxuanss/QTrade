import path from "node:path";
import type { NextConfig } from "next";

const config: NextConfig = {
  turbopack: { root: path.resolve(import.meta.dirname) },
  allowedDevOrigins: ["127.0.0.1"],
  output: "standalone",
  poweredByHeader: false,
  devIndicators: false,
};

export default config;
