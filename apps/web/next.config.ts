import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The `web` container (planning-v2.md D149) runs .next/standalone's server.js,
  // with only the files it needs; its Dockerfile sets this. Vercel, which
  // builds the demo, packages the app its own way, so it stays off there.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  cacheComponents: true,
  partialPrefetching: true,
  // Link-preview crawlers (LinkedIn, Facebook, X, Slack, WhatsApp, Discord)
  // are on Next's list of bots that get a fully rendered page, and on Vercel
  // that render failed with a 500 for every page (Googlebot and browsers were
  // fine), so a shared link showed no preview (D184). Every page's metadata
  // here is static and already in the <head> of the normal response, so they
  // get that: a pattern no user agent matches.
  htmlLimitedBots: /(?!)/,
  // The download page became the demo site's landing page (D180): links to
  // the old address keep working.
  async redirects() {
    return [{ source: "/download", destination: "/", permanent: false }];
  },
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
