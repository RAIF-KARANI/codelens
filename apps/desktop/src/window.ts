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

/** What a status page's buttons ask for. They are plain links to
 * codelens://<action>, which the window's navigation guard catches (main.ts),
 * so the page needs no script and no bridge into the app. */
export type AppAction =
  | "retry"
  | "start-docker"
  | "open-logs"
  | "continue"
  | "check-again"
  | "install-docker"
  | "install-ollama";

const ACTIONS: readonly AppAction[] = [
  "retry",
  "start-docker",
  "open-logs",
  "continue",
  "check-again",
  "install-docker",
  "install-ollama",
];

export function appAction(url: string): AppAction | null {
  const match = /^codelens:\/\/([a-z-]+)\/?$/.exec(url);
  const action = match?.[1] as AppAction | undefined;
  return action && ACTIONS.includes(action) ? action : null;
}

export type Button = { label: string; href: string };

export const escapeHtml = (text: string) =>
  text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] ?? c);

/** The page shown while the stack starts, or when it can't: what is going on
 * and what to do. Plain HTML from a data: URL; nothing in it comes from
 * outside but our own text. `busy` shows the pulsing dot. */
export function statusPage(
  title: string,
  detail: string,
  { buttons = [], busy = true }: { buttons?: Button[]; busy?: boolean } = {},
): string {
  const links = buttons
    .map((b, i) => `<a class="${i === 0 ? "primary" : ""}" href="${escapeHtml(b.href)}">${escapeHtml(b.label)}</a>`)
    .join("");
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>CodeLens</title>
<style>
  :root { color-scheme: light dark; --bg: #fbfaff; --fg: #1d1a2e; --muted: #6b6785; --accent: #6e56cf;
          --on-accent: #ffffff; --line: #dcd8ec; }
  @media (prefers-color-scheme: dark) { :root { --bg: #131120; --fg: #eeecf8; --muted: #a29fba;
          --accent: #9d8cf5; --on-accent: #131120; --line: #34304a; } }
  body { margin: 0; height: 100vh; display: grid; place-items: center; background: var(--bg); color: var(--fg);
         font: 15px/1.5 "Segoe UI", system-ui, sans-serif; }
  main { max-width: 36rem; padding: 2rem; text-align: center; }
  h1 { font-size: 1.25rem; font-weight: 600; margin: 0 0 .5rem; }
  p { color: var(--muted); margin: 0; white-space: pre-wrap; }
  .dot { width: .6rem; height: .6rem; border-radius: 50%; background: var(--accent); margin: 0 auto 1rem;
         animation: pulse 1.2s ease-in-out infinite; }
  .actions { margin-top: 1.5rem; display: flex; gap: .5rem; justify-content: center; flex-wrap: wrap; }
  a { padding: .5rem .9rem; border-radius: .5rem; border: 1px solid var(--line); color: var(--fg);
      text-decoration: none; font-weight: 500; }
  a.primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
  a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  @keyframes pulse { 50% { opacity: .3; } }
  @media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
</style></head><body><main>${busy ? '<div class="dot"></div>' : ""}<h1>${escapeHtml(title)}</h1>
<p>${escapeHtml(detail)}</p>${links ? `<div class="actions">${links}</div>` : ""}</main></body></html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}
