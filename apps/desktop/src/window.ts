/** What the app's window may show and open (no Electron here, so it can be
 * tested). The window shows the dashboard and nothing else: its key is in the
 * window's cookies, so a page from anywhere else must never load in it. */

/** The dashboard's address for this launch. 127.0.0.1, never localhost: the
 * key cookie belongs to exactly this host (D165). */
export function appOrigin(port: number): string {
  return `http://127.0.0.1:${port}`;
}

/** Is this URL the dashboard of this launch (same scheme, host and port)? */
export function isAppUrl(url: string, port: number): boolean {
  try {
    return new URL(url).origin === appOrigin(port);
  } catch {
    return false;
  }
}

/** A link the dashboard shows (GitHub, the docs) opens in the user's browser,
 * outside the app; only https, so no file:, javascript: or custom schemes. */
export function isExternalLink(url: string): boolean {
  try {
    return new URL(url).protocol === "https:";
  } catch {
    return false;
  }
}

/** The page shown while the stack starts, or when it can't. Plain HTML from a
 * data: URL; nothing in it comes from outside but our own text. */
export function statusPage(title: string, detail: string): string {
  const escape = (text: string) =>
    text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] ?? c);
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>CodeLens</title>
<style>
  :root { color-scheme: light dark; --bg: #fbfaff; --fg: #1d1a2e; --muted: #6b6785; --accent: #6e56cf; }
  @media (prefers-color-scheme: dark) { :root { --bg: #131120; --fg: #eeecf8; --muted: #a29fba; --accent: #9d8cf5; } }
  body { margin: 0; height: 100vh; display: grid; place-items: center; background: var(--bg); color: var(--fg);
         font: 15px/1.5 "Segoe UI", system-ui, sans-serif; }
  main { max-width: 34rem; padding: 2rem; text-align: center; }
  h1 { font-size: 1.25rem; font-weight: 600; margin: 0 0 .5rem; }
  p { color: var(--muted); margin: 0; white-space: pre-wrap; }
  .dot { width: .6rem; height: .6rem; border-radius: 50%; background: var(--accent); margin: 0 auto 1rem;
         animation: pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: .3; } }
  @media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
</style></head><body><main><div class="dot"></div><h1>${escape(title)}</h1><p>${escape(detail)}</p></main></body></html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}
