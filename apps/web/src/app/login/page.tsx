import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Suspense } from "react";

import { isDemo } from "@/lib/config";
import { first } from "@/lib/params";
import { safeNext } from "@/lib/paths";

export const metadata: Metadata = { title: "Sign in" };

const ERRORS: Record<string, string> = {
  denied: "GitHub sign-in was cancelled.",
  state: "That sign-in link expired or came from another browser. Please try again.",
  github: "GitHub didn't complete the sign-in. Please try again.",
};

export default function LoginPage({ searchParams }: PageProps<"/login">) {
  // An open demo has no sign-in (D117).
  if (isDemo()) redirect("/repos");
  return (
    <div className="mx-auto mt-16 max-w-sm text-center">
      <h1 className="text-2xl font-semibold tracking-tight">Sign in to CodeLens</h1>
      <p className="mt-3 text-sm text-muted-foreground">
        You&apos;ll see the repositories where the CodeLens GitHub App is installed and you have
        access. Signing in again picks up new installations.
      </p>
      {/* The query string is request data, so it renders behind a boundary
          and the rest of the page is prerendered. */}
      <Suspense fallback={<SignInButton href="/api/auth/login" />}>
        <SignIn searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function SignIn({ searchParams }: Pick<PageProps<"/login">, "searchParams">) {
  const { error, next } = await searchParams;
  const message = typeof error === "string" ? ERRORS[error] : undefined;
  const href = `/api/auth/login?${new URLSearchParams({ next: safeNext(first(next)) })}`;
  return (
    <>
      {message && (
        <p
          role="alert"
          className="mt-6 rounded-md border border-critical/30 bg-critical-soft px-3 py-2 text-sm text-critical"
        >
          {message}
        </p>
      )}
      <SignInButton href={href} />
    </>
  );
}

function SignInButton({ href }: { href: string }) {
  // A plain link, not <Link>: it leaves the app for GitHub.
  return (
    <a
      href={href}
      className="mt-8 inline-flex h-10 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/85"
    >
      <GitHubMark />
      Sign in with GitHub
    </a>
  );
}

function GitHubMark() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}
