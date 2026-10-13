import Link from "next/link";

/** Also what a repo or review you can't access looks like (D111). */
export default function NotFound() {
  return (
    <div className="mx-auto mt-16 max-w-md text-center">
      <h1 className="text-xl font-semibold tracking-tight">Not found</h1>
      <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">
        It doesn&apos;t exist, or you don&apos;t have access to it.
      </p>
      <Link href="/repos" className="mt-6 inline-block text-sm underline underline-offset-4">
        Back to repositories
      </Link>
    </div>
  );
}
