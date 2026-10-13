/** Where to go after login: only a path on this site. Anything else, such as
 * `https://evil.example` or `//evil.example` (which browsers treat as another
 * host), falls back to the repos page, so the login can't be used to send
 * someone elsewhere. */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) {
    return "/repos";
  }
  return next;
}
