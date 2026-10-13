import Link from "next/link";

/** Keyset pages go one way: from the newest, older and older. "Newest" goes
 * back to the start. Either link is null when there's nowhere to go. */
export function Pager({ newest, older }: { newest: string | null; older: string | null }) {
  if (!newest && !older) return null;
  const style =
    "rounded-md border border-zinc-300 px-3 py-1.5 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900";
  return (
    <nav aria-label="Pages" className="mt-4 flex justify-between text-sm">
      {newest ? (
        <Link href={newest} className={style}>
          ← Newest
        </Link>
      ) : (
        <span />
      )}
      {older && (
        <Link href={older} className={style}>
          Older →
        </Link>
      )}
    </nav>
  );
}
