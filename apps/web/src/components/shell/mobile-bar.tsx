"use client";

import { MenuIcon } from "lucide-react";
import Link from "next/link";
import { useState, type MouseEvent, type ReactNode } from "react";

import { Logo } from "@/components/logo";
import { SearchButton } from "@/components/shell/command-palette";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

/** The phone's top bar: the sidebar's content opens in a sheet from the left.
 * `children` is that content, rendered on the server. Following a link, or
 * opening the palette, closes the sheet. */
export function MobileBar({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);

  function closeOnLeave(event: MouseEvent) {
    const target = event.target as HTMLElement;
    if (target.closest("a, [aria-label='Search']")) setOpen(false);
  }

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border bg-card/90 px-3 backdrop-blur md:hidden">
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>
          <Button variant="ghost" size="icon" aria-label="Open navigation">
            <MenuIcon />
          </Button>
        </SheetTrigger>
        <SheetContent side="left" className="w-72 gap-0 p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          {/* A click handler on a wrapper, not on each link: the links are the
              server's, and this only listens for them. */}
          <div className="h-full" onClickCapture={closeOnLeave}>
            {children}
          </div>
        </SheetContent>
      </Sheet>
      <Link href="/repos" aria-label="CodeLens, home">
        <Logo />
      </Link>
      <div className="ml-auto">
        <SearchButton compact />
      </div>
    </header>
  );
}
