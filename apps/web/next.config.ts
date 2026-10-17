import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The `web` container (planning-v2.md D149) runs .next/standalone's server.js,
  // with only the files it needs; its Dockerfile sets this. Vercel, which
  // builds the demo, packages the app its own way, so it stays off there.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  cacheComponents: true,
  partialPrefetching: true,
  images: {
    // GitHub avatars, in the header.
    remotePatterns: [{ protocol: "https", hostname: "avatars.githubusercontent.com" }],
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
