import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** One headline number: a label (sentence case), the value, and a line of
 * detail that says what it counts. The value keeps proportional figures: at
 * this size, tabular ones look loose. */
export function StatTile({
  label,
  value,
  detail,
  icon,
  className,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1 rounded-xl border border-border bg-card p-4", className)}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-muted-foreground">{label}</h3>
        {icon && (
          <span className="text-muted-foreground/70 [&_svg]:size-4" aria-hidden="true">
            {icon}
          </span>
        )}
      </div>
      <p className="text-3xl font-semibold tracking-tight">{value}</p>
      {detail && <div className="text-xs text-muted-foreground">{detail}</div>}
    </div>
  );
}
