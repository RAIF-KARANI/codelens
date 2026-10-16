import Link from "next/link";

import { Button } from "@/components/ui/button";

/** Keyset pages go one way: from the newest, older and older. "Newest" goes
 * back to the start. Either link is null when there's nowhere to go. */
export function Pager({ newest, older }: { newest: string | null; older: string | null }) {
  if (!newest && !older) return null;
  return (
    <nav aria-label="Pages" className="mt-4 flex justify-between text-sm">
      {newest ? (
        <Button variant="outline" asChild>
          <Link href={newest}>← Newest</Link>
        </Button>
      ) : (
        <span />
      )}
      {older && (
        <Button variant="outline" asChild>
          <Link href={older}>Older →</Link>
        </Button>
      )}
    </nav>
  );
}
