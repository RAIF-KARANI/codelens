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
          alt={user.login}
          width={28}
          height={28}
          className="rounded-full"
        />
      )}
      {/* On narrow screens the avatar alone says who is signed in. */}
      <span className="hidden text-foreground/85 sm:inline">{user.login}</span>
      <form action="/api/auth/logout" method="post">
        <button
          type="submit"
          className="whitespace-nowrap rounded-md px-2 py-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          Sign out
        </button>
      </form>
    </div>
  );
}
