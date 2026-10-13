"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/** Links styled as tabs; the one matching the current path is marked. */
export function Tabs({ tabs }: { tabs: { href: string; label: string }[] }) {
  const pathname = usePathname();
  return (
    <nav className="mt-5 flex gap-1 border-b border-zinc-200 text-sm dark:border-zinc-800">
      {tabs.map(({ href, label }) => {
        const current = pathname === href;
        return (
          <Link
            key={href}
            href={href}
            aria-current={current ? "page" : undefined}
            className={`-mb-px border-b-2 px-3 py-2 ${
              current
                ? "border-zinc-900 font-medium text-zinc-950 dark:border-zinc-100 dark:text-zinc-50"
                : "border-transparent text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
            }`}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
