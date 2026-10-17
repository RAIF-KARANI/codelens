import type { ReactNode } from "react";

import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { cn } from "@/lib/utils";

/** What a page shows when there is nothing to show, or something went wrong:
 * an icon, what happened, and what to do about it. Every empty and error
 * state in the dashboard is one of these, so they read the same way. */
export function StateMessage({
  icon,
  title,
  children,
  action,
  tone = "neutral",
  className,
}: {
  icon: ReactNode;
  title: string;
  /** What it means and what to do, in a sentence or two. */
  children?: ReactNode;
  /** A link or button that does the next thing. */
  action?: ReactNode;
  tone?: "neutral" | "error";
  className?: string;
}) {
  return (
    <Empty
      className={cn(
        "border border-dashed border-input bg-card/50 py-10",
        tone === "error" && "border-critical/30",
        className,
      )}
    >
      <EmptyHeader>
        <EmptyMedia
          variant="icon"
          className={cn(
            tone === "error" ? "bg-critical-soft text-critical" : "bg-accent text-accent-foreground",
          )}
        >
          {icon}
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        {children && <EmptyDescription>{children}</EmptyDescription>}
      </EmptyHeader>
      {action && <EmptyContent>{action}</EmptyContent>}
    </Empty>
  );
}
