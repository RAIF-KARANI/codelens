"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { isActive } from "@/lib/nav";
import { cn } from "@/lib/utils";

type Props = { href: string; label: string; match: readonly string[]; icon: ReactNode };

/** A sidebar link, marked while the current page is one it owns (`match`).
 * The path is request-time data, so the prerendered shell shows the link
 * unmarked and the mark streams in (Next 16 requires the `<Suspense>`). */
export function NavLink(props: Props) {
  return (
    <Suspense fallback={<NavLinkView {...props} active={false} />}>
      <ActiveNavLink {...props} />
    </Suspense>
  );
}

function ActiveNavLink(props: Props) {
  return <NavLinkView {...props} active={isActive(usePathname(), props)} />;
}

/** Collapsed, only the icon shows, and the label moves into a tooltip. */
function NavLinkView({ href, label, icon, active }: Props & { active: boolean }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          href={href}
          aria-current={active ? "page" : undefined}
          className={cn(
            "flex h-8 min-w-0 items-center gap-2.5 rounded-md px-2 text-sm transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/60 collapsed:justify-center collapsed:px-0",
            active
              ? "bg-accent font-medium text-accent-foreground"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <span className="flex size-4 shrink-0 items-center justify-center [&_svg]:size-4">{icon}</span>
          <span className="truncate collapsed:sr-only">{label}</span>
        </Link>
      </TooltipTrigger>
      <TooltipContent side="right" className="hidden collapsed:block">
        {label}
      </TooltipContent>
    </Tooltip>
  );
}
