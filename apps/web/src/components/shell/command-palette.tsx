"use client";

import { FolderGit2Icon, GitPullRequestIcon, MonitorIcon, MoonIcon, SearchIcon, SunIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { Kbd } from "@/components/ui/kbd";
import { REPO_DOT } from "@/lib/nav";
import type { PaletteData } from "@/lib/palette";
import { setThemeChoice } from "@/lib/use-theme";
import { cn } from "@/lib/utils";

const OpenPalette = createContext<() => void>(() => {});

type Load = { state: "idle" | "loading" | "failed" } | { state: "ready"; data: PaletteData };

/** The ⌘K / Ctrl+K command palette: go to a repo or a recent review, or switch
 * the theme. Its data comes from /api/palette the first time it opens (and
 * again after a failure), so pages pay nothing for it. */
export function PaletteProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [load, setLoad] = useState<Load>({ state: "idle" });
  const loading = useRef(false);
  const router = useRouter();

  const fetchData = useCallback(async () => {
    if (loading.current) return;
    loading.current = true;
    setLoad({ state: "loading" });
    try {
      const response = await fetch("/api/palette", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setLoad({ state: "ready", data: (await response.json()) as PaletteData });
    } catch {
      setLoad({ state: "failed" });
    } finally {
      loading.current = false;
    }
  }, []);

  const show = useCallback(() => {
    setOpen(true);
    if (load.state === "idle" || load.state === "failed") void fetchData();
  }, [load.state, fetchData]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        if (open) setOpen(false);
        else show();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, show]);

  function go(href: string) {
    setOpen(false);
    router.push(href);
  }

  function theme(choice: "system" | "light" | "dark") {
    setOpen(false);
    setThemeChoice(choice);
  }

  const data = load.state === "ready" ? load.data : null;
  return (
    <OpenPalette.Provider value={show}>
      {children}
      <CommandDialog
        open={open}
        onOpenChange={setOpen}
        title="Command palette"
        description="Go to a repository or a review, or switch the theme."
        className="sm:max-w-xl"
      >
        {/* This CommandDialog is only the dialog: cmdk's root goes inside it. */}
        <Command>
          <CommandInput placeholder="Search repositories and reviews…" />
          <CommandList>
            <CommandEmpty>
              {load.state === "loading"
                ? "Loading…"
                : load.state === "failed"
                  ? "Couldn't load your repositories. Close and open the palette to try again."
                  : "Nothing found."}
            </CommandEmpty>
            <CommandGroup heading="Go to">
              <CommandItem value="All repositories" onSelect={() => go("/repos")}>
                <FolderGit2Icon />
                All repositories
              </CommandItem>
              {data?.repos.map((repo) => (
                <CommandItem
                  key={repo.id}
                  value={`repo ${repo.id} ${repo.full_name}`}
                  keywords={["repository", repo.status]}
                  onSelect={() => go(`/repos/${repo.id}`)}
                >
                  <span
                    className={cn("mx-1 size-2 shrink-0 rounded-full", REPO_DOT[repo.status])}
                    aria-hidden="true"
                  />
                  <span className="truncate">{repo.full_name}</span>
                  <CommandShortcut>{repo.status}</CommandShortcut>
                </CommandItem>
              ))}
            </CommandGroup>
            {data && data.reviews.length > 0 && (
              <CommandGroup heading="Recent reviews">
                {data.reviews.map((review) => (
                  <CommandItem
                    key={review.id}
                    value={`review ${review.id} ${review.label} ${review.repo}`}
                    keywords={["review", review.pr_number !== null ? `#${review.pr_number}` : ""]}
                    onSelect={() => go(`/reviews/${review.id}`)}
                  >
                    <GitPullRequestIcon />
                    <span className="truncate">{review.label}</span>
                    <CommandShortcut className="truncate">
                      {review.repo}
                      {review.pr_number !== null && ` · #${review.pr_number}`}
                    </CommandShortcut>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
            <CommandGroup heading="Theme">
              <CommandItem value="Theme: system" onSelect={() => theme("system")}>
                <MonitorIcon />
                Use the system theme
              </CommandItem>
              <CommandItem value="Theme: light" onSelect={() => theme("light")}>
                <SunIcon />
                Light theme
              </CommandItem>
              <CommandItem value="Theme: dark" onSelect={() => theme("dark")}>
                <MoonIcon />
                Dark theme
              </CommandItem>
            </CommandGroup>
          </CommandList>
        </Command>
      </CommandDialog>
    </OpenPalette.Provider>
  );
}

/** "⌘" on Apple devices, "Ctrl" elsewhere (and on the server, which can't know). */
function useModifierKey(): string {
  return useSyncExternalStore(
    () => () => {},
    () => (/Mac|iPhone|iPad/.test(navigator.userAgent) ? "⌘" : "Ctrl"),
    () => "Ctrl",
  );
}

/** Opens the palette: a search field in the sidebar, an icon on a phone. */
export function SearchButton({ compact = false }: { compact?: boolean }) {
  const open = useContext(OpenPalette);
  const mod = useModifierKey();
  if (compact) {
    return (
      <button
        type="button"
        onClick={open}
        aria-label="Search"
        className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
      >
        <SearchIcon className="size-4" />
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={open}
      aria-label="Search"
      className="flex h-8 w-full items-center gap-2 rounded-md border border-input bg-background px-2 text-sm text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:outline-none collapsed:justify-center collapsed:border-transparent collapsed:bg-transparent collapsed:px-0"
    >
      <SearchIcon className="size-4 shrink-0" />
      <span className="truncate collapsed:sr-only">Search…</span>
      <Kbd className="ml-auto collapsed:hidden">{mod} K</Kbd>
    </button>
  );
}
