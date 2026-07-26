import type { NextConfig } from "next";

// Catalyst Web Client Hosting serves the app under /app/...
const isCatalyst = process.env.CATALYST_HOSTING === "1";

const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
  ...(isCatalyst
    ? {
        basePath: "/app",
        assetPrefix: "/app",
      }
    : {}),
  transpilePackages: ["react-force-graph-2d", "echarts-for-react"],
};

export default nextConfig;
