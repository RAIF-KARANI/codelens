/** The first-run wizard's screen (planning-v2.md §4, Phase 11 step 5): the
 * steps and where it is, what is happening now, the machine's checks, a
 * progress bar, and buttons. Like the status pages, it's plain HTML from a
 * data: URL with no script of its own: its buttons are codelens:// links the
 * window carries out, and the app moves the progress bar by setting it from
 * outside (`progressScript`). */

import type { Check } from "./machine.js";
import { type Button, escapeHtml } from "./window.js";

export type StepId = "machine" | "docker" | "ollama" | "start";
export type Mark = "todo" | "active" | "done" | "warn" | "failed";

export const STEPS: { id: StepId; label: string }[] = [
  { id: "machine", label: "Check this computer" },
  { id: "docker", label: "Docker Desktop" },
  { id: "ollama", label: "Ollama" },
  { id: "start", label: "Start CodeLens" },
];

export type WizardView = {
  marks: Partial<Record<StepId, Mark>>;
  title: string;
  detail: string;
  checks?: Check[];
  /** `fraction` null: under way, size unknown. */
  progress?: { label: string; fraction: number | null };
  buttons?: Button[];
};

const MARK_TEXT: Record<Mark, string> = {
  todo: "",
  active: "now",
  done: "done",
  warn: "done, with a warning",
  failed: "needs you",
};
const CHECK_ICON = { ok: "✓", warn: "!", fail: "✕" } as const;

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
}

/** The progress line under the bar: "1.20 GB of 1.48 GB". */
export function progressLabel(received: number, total: number | null): string {
  return total ? `${formatBytes(received)} of ${formatBytes(total)}` : formatBytes(received);
}

export function wizardPage(view: WizardView): string {
  const steps = STEPS.map(({ id, label }) => {
    const mark = view.marks[id] ?? "todo";
    const state = MARK_TEXT[mark] ? `<span class="sr">: ${MARK_TEXT[mark]}</span>` : "";
    return `<li class="${mark}"${mark === "active" ? ' aria-current="step"' : ""}>${escapeHtml(label)}${state}</li>`;
  }).join("");
  const checks = view.checks?.length
    ? `<ul class="checks">${view.checks
        .map(
          (c) =>
            `<li class="${c.status}"><span class="icon" aria-hidden="true">${CHECK_ICON[c.status]}</span>` +
            `<span><b>${escapeHtml(c.name)}</b> ${escapeHtml(c.detail)}` +
            `${c.fix ? `<br><span class="fix">${escapeHtml(c.fix)}</span>` : ""}</span></li>`,
        )
        .join("")}</ul>`
    : "";
  const progress = view.progress
    ? `<div class="progress"><div class="track" role="progressbar" aria-label="${escapeHtml(view.progress.label)}"` +
      `${view.progress.fraction === null ? "" : ` aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(view.progress.fraction * 100)}"`}>` +
      `<div id="bar" class="${view.progress.fraction === null ? "bar busy" : "bar"}" style="width:${view.progress.fraction === null ? 30 : Math.round(view.progress.fraction * 100)}%"></div></div>` +
      `<p id="progress-label" class="small">${escapeHtml(view.progress.label)}</p></div>`
    : "";
  const buttons = (view.buttons ?? [])
    .map((b, i) => `<a class="${i === 0 ? "primary" : ""}" href="${escapeHtml(b.href)}">${escapeHtml(b.label)}</a>`)
    .join("");
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Set up CodeLens</title>
<style>
  :root { color-scheme: light dark; --bg: #fbfaff; --card: #ffffff; --fg: #1d1a2e; --muted: #6b6785;
          --accent: #6e56cf; --on-accent: #ffffff; --line: #dcd8ec; --ok: #1a7f37; --warn: #9a6700; --fail: #cf222e; }
  @media (prefers-color-scheme: dark) { :root { --bg: #131120; --card: #1b1830; --fg: #eeecf8; --muted: #a29fba;
          --accent: #9d8cf5; --on-accent: #131120; --line: #34304a; --ok: #3fb950; --warn: #d29922; --fail: #f85149; } }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg); color: var(--fg);
         font: 15px/1.5 "Segoe UI", system-ui, sans-serif; }
  main { display: grid; grid-template-columns: 13rem minmax(0, 34rem); gap: 2.5rem; padding: 2rem; }
  ol { list-style: none; margin: 0; padding: 0; counter-reset: step; }
  ol li { counter-increment: step; padding: .45rem 0 .45rem 2.2rem; position: relative; color: var(--muted); }
  ol li::before { content: counter(step); position: absolute; left: 0; top: .4rem; width: 1.5rem; height: 1.5rem;
                  border-radius: 50%; border: 1px solid var(--line); display: grid; place-items: center; font-size: .8rem; }
  ol li.active { color: var(--fg); font-weight: 600; }
  ol li.active::before { border-color: var(--accent); background: var(--accent); color: var(--on-accent); }
  ol li.done::before, ol li.warn::before { content: "✓"; border-color: var(--ok); color: var(--ok); }
  ol li.warn::before { border-color: var(--warn); color: var(--warn); }
  ol li.failed { color: var(--fg); }
  ol li.failed::before { content: "!"; border-color: var(--fail); color: var(--fail); }
  h1 { font-size: 1.35rem; font-weight: 600; margin: 0 0 .5rem; }
  p { color: var(--muted); margin: 0; white-space: pre-wrap; }
  .small { font-size: .85rem; margin-top: .4rem; }
  .checks { list-style: none; padding: 0; margin: 1.25rem 0 0; border: 1px solid var(--line); border-radius: .6rem; background: var(--card); }
  .checks li { display: flex; gap: .6rem; padding: .55rem .8rem; border-top: 1px solid var(--line); }
  .checks li:first-child { border-top: 0; }
  .checks .icon { width: 1rem; font-weight: 700; }
  .checks .ok .icon { color: var(--ok); } .checks .warn .icon { color: var(--warn); } .checks .fail .icon { color: var(--fail); }
  .fix { color: var(--muted); font-size: .85rem; }
  .progress { margin-top: 1.25rem; }
  .track { height: .5rem; border-radius: 99px; background: var(--line); overflow: hidden; }
  .bar { height: 100%; background: var(--accent); transition: width .3s; }
  .bar.busy { animation: slide 1.4s ease-in-out infinite; }
  @keyframes slide { 0% { margin-left: -30%; } 100% { margin-left: 100%; } }
  .actions { margin-top: 1.5rem; display: flex; gap: .5rem; flex-wrap: wrap; }
  a { padding: .5rem .9rem; border-radius: .5rem; border: 1px solid var(--line); color: var(--fg); text-decoration: none; font-weight: 500; }
  a.primary { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
  a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  @media (prefers-reduced-motion: reduce) { .bar { transition: none; } .bar.busy { animation: none; } }
  @media (max-width: 760px) { main { grid-template-columns: 1fr; gap: 1.5rem; } }
</style></head><body><main>
<nav aria-label="Setup steps"><ol>${steps}</ol></nav>
<section><h1>${escapeHtml(view.title)}</h1><p>${escapeHtml(view.detail)}</p>${checks}${progress}
${buttons ? `<div class="actions">${buttons}</div>` : ""}</section></main></body></html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

/** What the app runs in the page to move its progress bar without reloading
 * it. Both values are serialised as JSON, so a label can't become code. */
export function progressScript(fraction: number | null, label: string): string {
  const width = fraction === null ? null : Math.max(0, Math.min(100, Math.round(fraction * 100)));
  return `(() => { const bar = document.getElementById("bar"); const text = document.getElementById("progress-label");
  const width = ${JSON.stringify(width)}; if (bar && width !== null) { bar.classList.remove("busy"); bar.style.width = width + "%"; }
  if (text) text.textContent = ${JSON.stringify(label)}; })()`;
}
