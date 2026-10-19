import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { HEAD_SCRIPT } from "@/lib/head-script";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  // Shared links need absolute URLs (the Open Graph image, opengraph-image.tsx):
  // the demo site's address unless SITE_URL says otherwise.
  metadataBase: new URL(process.env.SITE_URL ?? "https://codelens.raifkarani.in"),
  title: { default: "CodeLens", template: "%s · CodeLens" },
  description: "Graph-aware code reviews: every finding with its evidence.",
  openGraph: { siteName: "CodeLens", type: "website" },
  twitter: { card: "summary_large_image" },
};

/** What every page has: the fonts, the theme, tooltips and toasts. The
 * dashboard's shell is its own layout, `(dashboard)/layout.tsx`, and the demo
 * site's landing page has another, `(landing)/layout.tsx` (D180). The theme and
 * the sidebar's collapsed state are applied by an inline script before the
 * first paint (lib/head-script.ts), which is why <html> may differ from the
 * server's HTML. */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      data-theme="light"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: HEAD_SCRIPT }} />
      </head>
      <body className="min-h-full">
        <TooltipProvider>
          {children}
          <Toaster />
        </TooltipProvider>
      </body>
    </html>
  );
}
