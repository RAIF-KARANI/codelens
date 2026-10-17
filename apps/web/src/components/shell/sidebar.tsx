import { FolderGit2Icon, GitPullRequestIcon, LayoutDashboardIcon, SettingsIcon } from "lucide-react";
import Link from "next/link";
import { connection } from "next/server";
import { cache, Suspense } from "react";

import { UserMenu } from "@/app/user-menu";
import { Logo } from "@/components/logo";
import { SearchButton } from "@/components/shell/command-palette";
import { NavLink } from "@/components/shell/nav-link";
import { SidebarToggle } from "@/components/shell/sidebar-toggle";
import { ThemeToggle } from "@/components/theme-toggle";
import { Skeleton } from "@/components/ui/skeleton";
import { callApi, type Repo } from "@/lib/api";
import { isLocal } from "@/lib/config";
import { NAV_ITEMS, REPO_DOT, sidebarRepos } from "@/lib/nav";
import { requestUser } from "@/lib/session";
import { cn } from "@/lib/utils";

const NAV_ICONS: Record<string, React.ReactNode> = {
  "/": <LayoutDashboardIcon />,
  "/reviews": <GitPullRequestIcon />,
  "/repos": <FolderGit2Icon />,
};

/** The repos the caller can see, once per request (the desktop sidebar and the
 * phone's sheet both list them), or null when signed out or the API is down:
 * the sidebar must never break a page. */
const visibleRepos = cache(async (): Promise<Repo[] | null> => {
  // Request-time data, even where nothing here reads the request (a demo's
  // visitor needs no cookie): the API token it mints reads the clock.
  await connection();
  const user = await requestUser();
  if (!user) return null;
  try {
    return await callApi<Repo[]>(user, "/repos");
  } catch {
    return null;
  }
});

/** What the sidebar holds, on a desktop and in the phone's sheet: the logo, the
 * search field, where to go, your repos, then the theme and your account. */
export function SidebarContent() {
  return (
    <div className="flex h-full min-h-0 flex-col gap-4 p-3">
      <div className="flex h-8 items-center px-1 collapsed:justify-center collapsed:px-0">
        <Link href="/" aria-label="CodeLens, home" className="rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/60">
          <Logo />
        </Link>
      </div>
      <SearchButton />
      <nav aria-label="Main" className="flex flex-col gap-0.5">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.href} {...item} icon={NAV_ICONS[item.href]} />
        ))}
        <Suspense fallback={null}>
          <LocalNav />
        </Suspense>
      </nav>
      <Suspense fallback={<RepoListSkeleton />}>
        <SidebarRepos />
      </Suspense>
      <div className="mt-auto flex flex-col gap-2 border-t border-border pt-3">
        <Suspense fallback={null}>
          <UserMenu />
        </Suspense>
        <div className="flex items-center justify-between collapsed:flex-col collapsed:gap-1">
          <ThemeToggle />
          <SidebarToggle />
        </div>
      </div>
    </div>
  );
}

async function SidebarRepos() {
  const repos = await visibleRepos();
  if (!repos || repos.length === 0) return null;
  const shown = sidebarRepos(repos);
  return (
    <section aria-labelledby="sidebar-repos" className="flex min-h-0 flex-col gap-0.5 collapsed:hidden">
      <h2 id="sidebar-repos" className="px-2 pb-1 text-xs font-medium text-muted-foreground">
        Repositories
      </h2>
      <div className="flex min-h-0 flex-col gap-0.5 overflow-y-auto">
        {shown.map((repo) => (
          <NavLink
            key={repo.id}
            href={`/repos/${repo.id}`}
            label={repo.full_name}
            match={[`/repos/${repo.id}`]}
            icon={<span className={cn("size-2 rounded-full", REPO_DOT[repo.status])} aria-hidden="true" />}
          />
        ))}
      </div>
      {repos.length > shown.length && (
        <Link href="/repos" className="px-2 pt-1 text-xs text-muted-foreground hover:text-foreground">
          All {repos.length} repositories →
        </Link>
      )}
    </section>
  );
}

function RepoListSkeleton() {
  return (
    <div className="flex flex-col gap-1.5 px-2 collapsed:hidden" aria-hidden="true">
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-6" />
      <Skeleton className="h-6" />
    </div>
  );
}

/** CodeLens Desktop's own pages (D175). Whether this is the desktop is known
 * where the dashboard runs, not when its image is built, so this part of the
 * sidebar renders per request. */
async function LocalNav() {
  await connection();
  if (!isLocal()) return null;
  return <NavLink href="/settings" label="Settings" match={["/settings"]} icon={<SettingsIcon />} />;
}
