import Link from "next/link";

import { LogoMark } from "@/components/logo";
import { ThemeToggle } from "@/components/theme-toggle";

/** The demo site's front page (D180): its own plain frame, no sidebar. The
 * dashboard's pages keep theirs, in `(dashboard)/`. */
export default function LandingLayout({ children }: LayoutProps<"/">) {
  return (
    <div className="flex min-h-svh flex-col">
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-4 px-4 md:px-8">
          <Link
            href="/"
            className="flex items-center gap-2 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            <LogoMark />
            <span className="text-[15px] font-semibold tracking-tight">CodeLens</span>
          </Link>
          <nav aria-label="Site" className="ml-auto flex items-center gap-1 text-sm">
            <Link href="/overview" className="rounded-md px-2.5 py-1.5 text-muted-foreground hover:text-foreground">
              Demo
            </Link>
            <a
              href="https://github.com/RAIF-KARANI/codelens/blob/main/docs/desktop.md"
              className="rounded-md px-2.5 py-1.5 text-muted-foreground hover:text-foreground"
            >
              Guide
            </a>
            <a
              href="https://github.com/RAIF-KARANI/codelens"
              className="rounded-md px-2.5 py-1.5 text-muted-foreground hover:text-foreground"
            >
              GitHub
            </a>
            <ThemeToggle />
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10 md:px-8 md:py-14">{children}</main>
      <footer className="border-t border-border">
        <p className="mx-auto w-full max-w-6xl px-4 py-6 text-sm text-muted-foreground md:px-8">
          CodeLens is open source (MIT), built by{" "}
          <a href="https://github.com/RAIF-KARANI" className="underline underline-offset-4 hover:text-foreground">
            Raif Karani
          </a>
          . The demo&apos;s reviews were made on a laptop GPU and copied here; nothing runs on this server but the pages.
        </p>
      </footer>
    </div>
  );
}
