const FALLBACK = "/repos";
// Any origin works: it only lets the URL parser resolve a path the same way the
// redirect will, and `.invalid` can never be a real host.
const PLACEHOLDER = "http://codelens.invalid";

/** Where to go after login: only a path on this site. Anything else falls back to
 * the repos page, so the login can't be used to send someone elsewhere. That
 * includes `https://evil.example` and `//evil.example` (browsers treat it as
 * another host), but also `/<TAB>/evil.example`: the URL parser drops tabs and
 * newlines, which leaves `//evil.example` (D128). So the parser decides here,
 * the same one the redirect uses, rather than a check on the raw string. */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || /[\u0000-\u001f\u007f\\]/.test(next)) {
    return FALLBACK;
  }
  let url: URL;
  try {
    url = new URL(next, PLACEHOLDER);
  } catch {
    return FALLBACK;
  }
  if (url.origin !== PLACEHOLDER) return FALLBACK;
  const path = url.pathname + url.search + url.hash;
  // Dot segments can still normalize into another host: `/.//evil.example`
  // becomes `//evil.example`.
  return path.startsWith("//") ? FALLBACK : path;
}

/** The absolute post-login redirect: `next` made safe, then checked once more
 * against the dashboard's own origin, so a future change to `safeNext` can't
 * turn the login into an open redirect on its own. */
export function postLoginUrl(next: string | null | undefined, appUrl: string): URL {
  const url = new URL(safeNext(next), appUrl);
  return url.origin === new URL(appUrl).origin ? url : new URL(FALLBACK, appUrl);
}
