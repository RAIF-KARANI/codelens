import { LogOutIcon } from "lucide-react";
import Image from "next/image";

import { Button } from "@/components/ui/button";
import { optionalUser } from "@/lib/dal";

/** The signed-in user and a sign-out button, at the foot of the sidebar;
 * nothing when signed out (or on the demo, where nobody signs in). Collapsed,
 * the avatar alone says who is signed in. */
export async function UserMenu() {
  const user = await optionalUser();
  if (!user) {
    return null;
  }
  return (
    <div className="flex min-w-0 items-center gap-2 px-1 text-sm collapsed:flex-col collapsed:px-0">
      {user.avatarUrl && (
        <Image
          src={user.avatarUrl}
          alt={user.login}
          width={24}
          height={24}
          className="shrink-0 rounded-full"
        />
      )}
      <span className="min-w-0 flex-1 truncate text-foreground/85 collapsed:sr-only">{user.login}</span>
      <form action="/api/auth/logout" method="post">
        <Button type="submit" variant="ghost" size="icon-sm" aria-label="Sign out" title="Sign out">
          <LogOutIcon />
        </Button>
      </form>
    </div>
  );
}
