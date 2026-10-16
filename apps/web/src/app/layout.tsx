import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { Suspense } from "react";

import { UserMenu } from "@/app/user-menu";
import { Logo } from "@/components/logo";
import { ThemeToggle } from "@/components/theme-toggle";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { isDemo } from "@/lib/config";
import { THEME_SCRIPT } from "@/lib/theme";
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

/** The shell every page shares. It reads no request data itself, so it's
 * prerendered; the signed-in user streams in behind its own boundary. The
 * theme is applied by an inline script before the first paint (lib/theme.ts),
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
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="flex min-h-full flex-col">
        <TooltipProvider>
          <header className="border-b border-border bg-card/80 backdrop-blur">
            <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-6 px-4">
              <Link href="/repos" aria-label="CodeLens, home">
                <Logo />
              </Link>
              <nav className="flex gap-4 text-sm text-muted-foreground">
                <Link href="/repos" className="hover:text-foreground">
                  Repositories
                </Link>
              </nav>
              <div className="ml-auto flex items-center gap-2">
                <ThemeToggle />
                <Suspense fallback={<div className="h-7 w-24" />}>
                  <UserMenu />
                </Suspense>
              </div>
            </div>
          </header>
          {isDemo() && <DemoBanner />}
          <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">{children}</main>
          <Toaster />
        </TooltipProvider>
      </body>
    </html>
  );
}

/** What a demo visitor is looking at. */
function DemoBanner() {
  return (
    <div className="border-b border-border bg-accent text-sm text-accent-foreground">
      <p className="mx-auto max-w-6xl px-4 py-2">
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
