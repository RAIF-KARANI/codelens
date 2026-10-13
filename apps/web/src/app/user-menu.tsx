import Image from "next/image";

import { optionalUser } from "@/lib/dal";

/** The signed-in user and a sign-out button; nothing when signed out. */
export async function UserMenu() {
  const user = await optionalUser();
  if (!user) {
    return null;
  }
  return (
    <div className="flex items-center gap-3 text-sm">
      {user.avatarUrl && (
        <Image
          src={user.avatarUrl}
          alt=""
          width={28}
          height={28}
          className="rounded-full"
        />
      )}
      <span className="text-zinc-700 dark:text-zinc-300">{user.login}</span>
      <form action="/api/auth/logout" method="post">
        <button
          type="submit"
          className="rounded-md px-2 py-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
        >
          Sign out
        </button>
      </form>
    </div>
  );
}
