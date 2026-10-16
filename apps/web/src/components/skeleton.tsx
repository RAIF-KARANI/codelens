import { Skeleton } from "@/components/ui/skeleton";

/** Placeholder rows while a table streams in. */
export function TableSkeleton({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <div className="mt-6 space-y-2" aria-busy="true" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-11" />
      ))}
    </div>
  );
}
