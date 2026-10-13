import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { Suspense } from "react";

import { UserMenu } from "@/app/user-menu";
import { isDemo } from "@/lib/config";
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
 * prerendered; the signed-in user streams in behind its own boundary. */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col">
        <header className="border-b border-zinc-200 dark:border-zinc-800">
          <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-6 px-4">
            <Link href="/repos" className="font-semibold tracking-tight">
              CodeLens
            </Link>
            <nav className="flex gap-4 text-sm text-zinc-600 dark:text-zinc-400">
              <Link href="/repos" className="hover:text-zinc-950 dark:hover:text-zinc-50">
                Repositories
              </Link>
            </nav>
            <div className="ml-auto">
              <Suspense fallback={<div className="h-7 w-24" />}>
                <UserMenu />
              </Suspense>
            </div>
          </div>
        </header>
        {isDemo() && <DemoBanner />}
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">{children}</main>
      </body>
    </html>
  );
}

/** What a demo visitor is looking at. */
function DemoBanner() {
  return (
    <div className="border-b border-sky-200 bg-sky-50 text-sm text-sky-900 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-100">
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
