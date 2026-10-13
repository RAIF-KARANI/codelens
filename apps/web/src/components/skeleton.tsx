/** Placeholder rows while a table streams in. */
export function TableSkeleton({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <div className="mt-6 space-y-2" aria-busy="true" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-11 animate-pulse rounded-md bg-zinc-100 dark:bg-zinc-900" />
      ))}
    </div>
  );
}
