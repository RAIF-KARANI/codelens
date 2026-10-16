import { InfoIcon } from "lucide-react";
import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { PaletteProvider } from "@/components/shell/command-palette";
import { MobileBar } from "@/components/shell/mobile-bar";
import { SidebarContent } from "@/components/shell/sidebar";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { isDemo } from "@/lib/config";
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
  title: { default: "CodeLens", template: "%s · CodeLens" },
  description: "Graph-aware code reviews: every finding with its evidence.",
};

/** The shell every page shares: a sidebar on a desktop, a top bar with a sheet
 * on a phone, and the ⌘K palette around both. It reads no request data itself,
 * so it's prerendered; the repo list and the signed-in user stream in behind
 * their own boundaries. The theme and the sidebar's collapsed state are applied
 * by an inline script before the first paint (lib/head-script.ts),
 * which is why <html> may differ from the server's HTML. */
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
          <PaletteProvider>
            <div className="flex min-h-svh">
              <aside className="sticky top-0 hidden h-svh w-60 shrink-0 border-r border-border bg-card transition-[width] duration-200 md:block collapsed:w-14">
                <SidebarContent />
              </aside>
              <div className="flex min-w-0 flex-1 flex-col">
                <MobileBar>
                  <SidebarContent />
                </MobileBar>
                <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 md:px-8 md:py-8">
                  {isDemo() && <DemoNotice />}
                  {children}
                </main>
              </div>
            </div>
          </PaletteProvider>
          <Toaster />
        </TooltipProvider>
      </body>
    </html>
  );
}

/** What a demo visitor is looking at. */
function DemoNotice() {
  return (
    <div className="mb-6 flex gap-2.5 rounded-lg border border-primary/20 bg-accent px-3.5 py-2.5 text-sm text-accent-foreground">
      <InfoIcon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <p>
        <span className="font-medium">Read-only demo.</span> These reviews were made by a local
        model (qwen3:8b) on a laptop GPU, then copied here; nothing runs on this server but the
        pages. Source and evaluation:{" "}
        <a href="https://github.com/RAIF-KARANI/codelens" className="underline underline-offset-4">
          github.com/RAIF-KARANI/codelens
        </a>
        .
      </p>
    </div>
  );
}
