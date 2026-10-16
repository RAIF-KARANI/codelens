# CodeLens v2: Engineering Plan

> **How this relates to the other documents:** v1 is finished. Its plan of record,
> [`planning.md`](planning.md), is kept as the record of how v1 was built (Phases 1–9,
> decisions D1–D137, questions Q1–Q20) and no longer changes. **This file is the plan of
> record for v2.** It adds Phases 10 and up, decisions D138 and up, and questions Q21 and up.
> Where the two disagree, this file wins. [`idea.md`](idea.md) is still the design
> narrative, and [`architecture.md`](architecture.md) still describes what is built (it
> changes as v2 lands).

**Last updated:** 2026-10-16 · **Current position:** planning. The eight decisions behind v2
were made by the user on 2026-10-16 (D138–D148); nothing in v2 is built yet. Next: Phase 10
step 1, which starts with a brand pass the user chooses from.

---

## Contents

0. [Summary](#0-summary)
1. [Where v1 ended](#1-where-v1-ended)
2. [Two products, one codebase](#2-two-products-one-codebase)
3. [Independent for whoever downloads it](#3-independent-for-whoever-downloads-it)
4. [The desktop app's architecture](#4-the-desktop-apps-architecture)
5. [The UI upgrade](#5-the-ui-upgrade)
6. [Build phases](#6-build-phases)
7. [Decisions](#7-decisions)
8. [Risks](#8-risks)
9. [Open questions](#9-open-questions)
10. [Conventions and definition of done](#10-conventions-and-definition-of-done)

---

## 0. Summary

v1 is an AI code reviewer for GitHub pull requests that runs on its owner's GPU: a code
graph, a local `qwen3:8b`, inline comments on the PR, and a dashboard that explains every
review. It is used two ways today: the public read-only demo, and a fresh clone set up with
`codelens doctor` and `codelens setup`.

**v2 has two goals:**

1. **A better UI** (Phase 10): a real design system, a bolder CodeLens brand, an app shell,
   an overview page, and a review page that puts the findings first. It ships to the
   public demo and to everyone running CodeLens.
2. **A downloadable desktop app** (Phase 11): **CodeLens Desktop**, a Windows installer.
   Someone downloads it, runs it, and a first-run wizard sets up everything else: Docker
   Desktop, Ollama, the models, the database, their own GitHub App. No terminal, no clone,
   no `.env` to edit.

Both are kept: **the web** (the hosted demo, and the clone-and-run setup for developers)
and **the software** (the desktop app). They share one codebase (§2). And whoever
downloads the software depends on nothing of the owner's: not the laptop, not the GitHub
App, not the demo, not any server (§3).

| Concern | v2 choice | Decision |
|---|---|---|
| UI components | shadcn/ui copied into the repo (Radix primitives), lucide icons | D140 |
| Look | A bolder CodeLens brand: accent color, logo mark, type, a little motion | D141 |
| Desktop shell | Electron | D142 |
| Database, queue, services | Docker Desktop required; the app drives its own Compose project | D143 |
| Background jobs | Redis + Celery, unchanged | D144 |
| GitHub events | Polling by default; webhooks through a relay optional | D145 |
| Models | The user's installed Ollama, or the official installer if missing | D146 |
| Dashboard sign-in (desktop) | None: the app is its single local owner | D147 |
| Platforms | Windows x64 first, unsigned; macOS and Linux later | D148 |

---

## 1. Where v1 ended

v1's definition of done (`planning.md` §19) is met: the GitHub App reviews PRs with inline
comments using local models; the dashboard shows live reviews, evidence, the call graph and
the retrieval trace; `evals/RESULTS.md` tells the measured naive → naive+ story; the demo is
live at <https://codelens.raifkarani.in>. Phase 8 made it runnable by someone else from a
fresh clone, and Phase 9 fixed every finding of the first security audit.

What v1 still asks of a new user, and what v2 removes:

| v1 asks the user to | v2 desktop |
|---|---|
| Install Git, uv, Docker Desktop and Ollama by hand | The wizard checks each one and installs Docker Desktop and Ollama if missing |
| Clone the repo, run `uv sync`, `codelens doctor`, `codelens setup` in a terminal | Double-click the installer; the wizard runs the same steps with buttons and progress bars |
| Run `pnpm dev` for the dashboard, after a separate one-time setup | The dashboard is the app's window |
| Sign in to the dashboard with GitHub, and set the App's redirect URI | No sign-in (D147) |
| Use a smee.io channel that a third party can read; lose events while the machine is off | Polling (D145): nothing goes through a third party, and the app catches up after sleep |
| Look in `docker compose logs` to see what's wrong | A System page with each service's health and logs |

v1's known gaps that v2 closes on the way: the dashboard isn't in Docker Compose
(architecture.md §15) and a failed review posts nothing; see D149 and Phase 11 step 4.

---

## 2. Two products, one codebase

```
                         packages/core · services/api · services/worker · apps/web
                                              │
            ┌─────────────────────────────────┼──────────────────────────────────┐
            ▼                                 ▼                                  ▼
   Hosted demo (web)               Self-hosted (web)                   CodeLens Desktop (software)
   Vercel + Supabase               a clone + `codelens setup`          apps/desktop: an Electron installer
   DEMO_MODE: read-only,           Docker Compose, the dashboard       drives its own Compose project;
   no sign-in, no models           in a browser, GitHub sign-in        LOCAL_MODE: no sign-in
   (unchanged since Phase 7)       (unchanged, plus the web            (new in Phase 11)
                                   container, D149)
```

- **One review engine.** The desktop app runs the same API, workers and pipeline images as
  the self-hosted stack. A review made in the desktop app is the same review, with the same
  measured F1.
- **One dashboard, three modes.** `apps/web` already has two modes (normal, `DEMO_MODE`).
  v2 adds `LOCAL_MODE` for the desktop (D147). Every Phase 10 page works in all three; the
  System and Settings pages appear only where they mean something (local mode, and, for an
  admin, the self-hosted dashboard).
- **New code lives where it belongs:** `apps/desktop/` (Electron: the shell, the
  supervisor, the wizard's native steps, packaging), `core/github/poll.py` (the poller),
  and the dashboard's new pages in `apps/web`.

---

## 3. Independent for whoever downloads it

The user's requirement (2026-10-16): the downloaded software is **independent of the owner
and the owner's services**. This is a rule every Phase 11 step is checked against (D139).

| A downloaded CodeLens Desktop never | Instead |
|---|---|
| Talks to the owner's laptop | Everything runs on the user's machine |
| Uses the owner's GitHub App | The wizard creates the user's own App from the manifest (D93), on their account |
| Uses the owner's smee channel, domain, Vercel, Supabase or Cloudflare | Polling needs no relay (D145); a relay, if wanted, is the user's own |
| Shares secrets with anyone | `API_JWT_SECRET`, the App's key and webhook secret are generated on the machine, written owner-only (D132) |
| Sends telemetry, crash reports or usage data | There is none |
| Stops working if the demo or the project's repo goes away | It needs only GitHub (for the user's own App and PRs), Docker and Ollama |

Its only outside connections:

- **GitHub**, as the user's own App: reading their repos and posting their reviews, which
  is the product.
- **Downloads it asks for:** Docker Desktop and Ollama installers from their publishers,
  the models from Ollama's registry, and the CodeLens container images (D143, D150).
- **Update checks** against the project's public GitHub Releases. They can be turned off,
  and an update is never applied without the user's yes (D151).

The software is MIT-licensed like the repo. Docker Desktop has its own terms: free for
personal use, education and small companies; larger companies need a paid plan. The
download page says so.

---

## 4. The desktop app's architecture

```mermaid
flowchart TB
    subgraph win["The user's Windows machine"]
        subgraph app["CodeLens Desktop (Electron)"]
            MAIN["main process<br/>supervisor · tray · updater · wizard steps"]
            WIN["window<br/>loads the dashboard"]
        end
        subgraph dd["Docker Desktop: Compose project codelens-desktop"]
            PG[("postgres + pgvector")]
            RD[("redis")]
            MIG["migrate"]
            API["api"]
            WI["worker (index)"]
            WG["worker-gpu"]
            POLL["poller"]
            WEB["web (Next.js standalone)"]
        end
        OL["Ollama (the user's own)<br/>qwen3:8b · qwen3-embedding:0.6b"]
    end
    GH(["GitHub<br/>the user's own App"])
    MAIN -- "docker compose up / down / ps" --> dd
    WIN -- "127.0.0.1:port + app key" --> WEB
    WEB --> API
    WG -- host.docker.internal --> OL
    POLL -- "conditional GETs (ETag)" --> GH
    WG -- "reviews" --> GH
```

**What each part does:**

| Part | Role |
|---|---|
| **Electron main process** | Starts and stops the Compose project, watches its health, restarts what fails, keeps logs, shows a tray icon, starts at login if asked, checks for updates. Runs the checks that must happen before Docker exists (Windows version, virtualization, Docker, Ollama, GPU, disk) |
| **The window** | Loads the dashboard from the `web` container on a loopback port. The first run shows the wizard instead |
| **The Compose project** | The same services as today's `docker-compose.yml`, plus `web` (D149) and `poller` (D145), under their own project name, data folder and free ports, so a developer's own stack on the same machine doesn't clash |
| **Data** | `%LOCALAPPDATA%\CodeLens\`: the mirrors (`data/`), the secrets (`secrets/`, owner-only), the generated `.env`, logs, database backups. The database itself is a named Docker volume |
| **Ollama** | The user's own installation, reached at `host.docker.internal:11434` as today (D146) |

**First run (the wizard):**

```mermaid
flowchart LR
    A[Welcome] --> B["Check the machine<br/>Windows, virtualization, GPU, disk"]
    B --> C{"Docker Desktop?"}
    C -- missing --> C1["Download the official installer,<br/>check its signature, run it<br/>(may need WSL2 and a restart;<br/>the wizard resumes after it)"]
    C -- ready --> D{"Ollama?"}
    C1 --> D
    D -- missing --> D1["Download, check, install"]
    D -- ready --> E["Pull the models<br/>(~6 GB, progress bar)"]
    D1 --> E
    E --> F["Start the stack<br/>(pull images, migrate, /health)"]
    F --> G["Create your GitHub App<br/>(the browser opens; one click)"]
    G --> H["Install it on your repos"]
    H --> I["Done: the dashboard"]
```

Each step is idempotent and resumable, like `codelens setup` (D126): closing the app
halfway, or a restart for Docker, picks up at the first step not yet done. Once the stack
runs, the remaining checks come from `codelens doctor` inside the API container, as JSON
(a new `--json` flag), so the wizard and the terminal share one source of truth.

**Day to day:** the app lives in the tray. Closing the window doesn't stop reviews.
**Pause** stops the workers; **Quit** stops the stack. On the next start it catches up on
PRs pushed meanwhile (D145).

---

## 5. The UI upgrade

### 5.1 What's wrong today

Measured against the live demo (2026-10-16):

- The review page stacks the timeline, the changed code and the token usage **above** the
  findings and the diff, so the thing a reader came for is below the fold.
- Every page is a single centered column with a top bar: no navigation beyond
  "Repositories", no way to see reviews across repos, no home page that says what's going
  on.
- Styles are ad-hoc Tailwind classes repeated per component (zinc grays, per-file severity
  colors). Dark mode follows the system and can't be chosen.
- The diff has no syntax highlighting; the live timeline is a list of sentences; the graph
  uses React Flow's default nodes.
- There is no brand beyond the word "CodeLens".

### 5.2 Direction

- **Components:** shadcn/ui, copied into `apps/web/src/components/ui/` (D140). The code is
  ours to read and change; Radix underneath gives keyboard and screen-reader behavior for
  dialogs, drawers, menus, tooltips and tabs. Plus `lucide-react` (icons), `sonner`
  (toasts), `cmdk` (the command palette) and Shiki (syntax highlighting, run on the
  server so no highlighter ships to the browser).
- **Brand (D141):** an accent color, a logo mark, a type scale and a few motion rules,
  chosen by the user from 2–3 options in step 1, defined once as CSS variables for light
  and dark. Severity colors stay distinct from the accent, so "critical" never looks like
  a button.
- **Tokens, not classes:** colors, radii, shadows and spacing come from the theme; pages
  use names (`bg-surface`, `text-muted`, `severity-critical`), not raw palette values.
- **Same data, same API:** apart from the overview (step 3), Phase 10 changes how things
  look and move, not what the API returns.

---

## 6. Build phases

Each phase is a series of numbered steps, one PR each, with a stop for review after each
(as in v1, `planning.md` §9). A step is done when its exit criteria hold, CI is green, and
the docs say what changed.

### Phase 10: UI upgrade

**Exit:** every existing page rebuilt on the shared design system and brand, the overview
and the cross-repo reviews list added, the demo live with the new UI, tests green, README
screenshots refreshed.

| Step | Deliverable | Exit criteria | User action |
|---|---|---|---|
| 0 | **This plan:** `planning-v2.md`; `planning.md` frozen as the v1 record (its Phase 9 status corrected); README, `idea.md`, `architecture.md` and `docs/` point to v2 | Docs only | Review the plan |
| 1 | **Brand and design foundation:** 2–3 brand options (accent, logo mark, type) shown as a page; the chosen one as tokens for light and dark; shadcn/ui set up; the primitives (button, badge, card, tabs, tooltip, sheet, dialog, dropdown, toast, skeleton, empty state, kbd); a theme toggle (system, light, dark); the existing components (`badges`, `tabs`, `pager`, `skeleton`) moved onto them | No page looks broken in either theme; vitest, lint and build green | Pick the brand |
| 2 | **App shell:** a collapsible sidebar (Overview, Repositories, Reviews; System and Settings slots for later), breadcrumbs, the user menu, the demo banner restyled, a ⌘K command palette (go to a repo or a review, switch theme); a sheet sidebar on phones | Every page reachable from the sidebar and the palette; keyboard only works | — |
| 3 | **Overview and cross-repo reviews:** `/` becomes an overview (stat tiles: reviews this week, findings by severity, median time to post, repos by status; recent reviews; what's running now); `/reviews` lists every visible repo's reviews with status and severity filters. API: `GET /api/v1/reviews` (keyset, across the caller's visible repos) and `GET /api/v1/stats`, with access tests (D111) | New endpoints tested for access and pagination; the demo's overview renders from its data | — |
| 4 | **The review page:** findings first. A header with the PR, status and severity summary; a file tree with finding counts; the diff highlighted by Shiki; a findings panel with severity and category filters; keyboard navigation (`j`/`k` next/previous finding, `e` evidence, `g` graph); the evidence in a side sheet, highlighted; run details (timeline, changed code, tokens) in a collapsible panel | A finding is visible without scrolling on a 1366×768 screen; every finding reachable by keyboard | — |
| 5 | **Live progress:** a running review shows a stepper (changes → context → generating chunk *i* of *n* → validated → posted) driven by the existing SSE events, with elapsed time; a toast when it ends; repo and review lists update their status live | Watched live on a real PR | Allow a test push |
| 6 | **Graph and trace:** custom React Flow nodes (kind icon, change color, seen or not, ★ evidence), a legend, a minimap, hover-highlighting a node's neighbours, code in the side sheet; trace budget bars, a sortable and filterable candidate table | Same data as today, nothing lost; graph-layout tests still pass | — |
| 7 | **Repo pages and the states between:** index stats as cards, run history, re-index with a toast; empty states that say what to do (no repos → install the App); 404 and error pages in the new style | Every empty and error state seen at least once | — |
| 8 | **Checks:** an accessibility pass (focus, contrast, reduced motion, labels), phone and tablet layouts, Playwright smoke and screenshot tests against the demo export, README screenshots and launch-video stills refreshed, the demo redeployed | Playwright in CI; the live demo shows the new UI | — |

### Phase 11: CodeLens Desktop (Windows)

**Exit:** on a Windows machine that has never seen CodeLens, a user downloads the
installer, follows the wizard, and gets a review posted on their own PR by their own App,
without a terminal, and without anything of the owner's involved (§3).

| Step | Deliverable | Exit criteria | User action |
|---|---|---|---|
| 1 | **Spike, then go/no-go:** `apps/desktop` (Electron) starts a Compose project of its own (project name, ports, data folder), shows the dashboard from a new `web` container (Next `output: 'standalone'`, D149), and stops it cleanly; container images built in CI and published with pinned digests (D143). Measured: installer size, time to a running stack, idle RAM | A short report with the numbers; go or no-go recorded here | Run the spike build once |
| 2 | **GitHub polling** (`core/github/poll.py`, a `poller` service, D145): per installation, the open PRs and the default branch's head, with conditional requests; changes go through the same intake as webhooks (shared functions, so allowlist, cap, supersede and dedupe rules are identical); catch-up on start. Webhooks stay, and the relay becomes a setting | Tests replay a PR opened, pushed, reopened and the default branch moving, each producing exactly one job; the webhook tests still pass | — |
| 3 | **Local mode** (`LOCAL_MODE`, D147): the dashboard calls the API as the local owner with no sign-in; the web server accepts only requests carrying the app's key (a cookie only the app's window holds) with a loopback Host; the API stays on loopback. A security review of the step, like Phase 9 | Requests from a browser tab, another Host, or without the key are refused (tests) | — |
| 4 | **Supervisor and tray:** start order and health (Docker running → stack up → `/health`), restart with backoff, logs per service in the data folder, clean shutdown, one instance only, tray (open, pause, quit), start at login (asked once); clear screens when Docker or Ollama stops. A failed review now says so on the PR (closing a v1 gap) | Killing a container, Docker or Ollama mid-run recovers or explains itself | — |
| 5 | **First-run wizard** (§4): the native checks; Docker Desktop and Ollama downloaded from their publishers and checked by signature before running (D150), with the restart Docker may need survived; the models pulled with progress; the stack started; the user's App created by the existing manifest flow (D93, D132) with the browser opened by the app; the install link; `codelens doctor --json` for the rest | Run on a machine with nothing installed and on one with everything; both end at the dashboard | — |
| 6 | **Settings and System pages** (local mode): models (installed, pulled, a button that applies the 16k-window settings to the user's Ollama after asking, D146), the App (install link, allowed accounts, review cap), events (polling interval, or a relay), repos, storage (disk used, clear mirrors), each service's health and logs, open the data folder | Every setting round-trips and survives a restart | — |
| 7 | **Packaging and updates:** an NSIS installer for Windows x64 built in CI on a version tag, published to GitHub Releases; unsigned (D148), with a download page explaining SmartScreen's "Run anyway"; update checks against Releases, applied only on the user's yes, with a database backup before new migrations (D151); an uninstaller that asks whether to delete the data and links to deleting the App | Install, update from the previous version, and uninstall all work on a clean VM | Create the first release tag |
| 8 | **Fresh-machine rehearsal:** a Windows machine (or VM with a GPU) that has never seen CodeLens, from the download page to a posted review, timed, with every confusing moment fixed; `docs/desktop.md` (the user's guide) | The exit criterion above, met and recorded | Do the rehearsal with a throwaway App |

### Later (not planned yet)

macOS (Apple silicon, signed and notarized) and Linux builds · code signing for Windows ·
the hosted service (`docs/hosted-service.md`) · the deferred retrieval upgrades U1–U5,
waiting for a model that uses context (`planning.md` Q2).

---

## 7. Decisions

Numbered after v1's (D1–D137 in `planning.md` §16 and `idea.md` §15).

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D138 | v2 is two products on one codebase: the web (the hosted demo and the clone-and-run setup, both kept) and the desktop app; `planning-v2.md` is the plan of record, and `planning.md` is frozen as v1's record | Replace the web setup with the desktop app; keep extending `planning.md` | The user wants both (2026-10-16). One engine means one measured F1 and one set of tests for every product. A new file keeps v1's story readable as it was, and v2's decisions start from a clean page |
| D139 | Whoever downloads the software depends on nothing of the owner's: own App, own secrets, own data and models, no relay of the owner's, no telemetry; outside connections are GitHub, the downloads the user approves, and opt-out update checks (§3) | Ship with the owner's App preinstalled; a hosted relay run by the project | The user's requirement (2026-10-16). The owner's laptop or accounts would become a single point of failure, a privacy problem (strangers' code passing through them) and a cost. Phase 8 already proved a user can have their own App in one click |
| D140 | UI built on shadcn/ui copied into the repo, with Radix primitives, lucide icons, sonner, cmdk and Shiki on the server | Hand-rolled Tailwind components (today); a component library installed as a dependency (MUI, Chakra) | User's choice (2026-10-16). Copied components are code we own and can explain, like the rest of the project, while Radix handles the accessibility details (focus traps, keyboard, ARIA) that hand-rolled dialogs and menus usually get wrong. Server-side Shiki highlights without shipping a highlighter to the browser |
| D141 | A bolder CodeLens brand: accent color, logo mark, type scale and motion rules, picked by the user from 2–3 options, as tokens for light and dark; severity colors kept apart from the accent | Polish the current neutral look | User's choice (2026-10-16): the project is a portfolio piece and should be recognisable. Choosing from options keeps the decision with the user; tokens make the brand one file to change |
| D142 | The desktop shell is Electron (`apps/desktop`) | Tauri; a tray program that opens the user's browser | User's choice (2026-10-16). The dashboard needs a Node server (Server Components, route handlers, Server Actions), and Electron ships Node; its installer, update and tray tooling is mature. Tauri would add Rust and still need a Node sidecar. A plain browser tab on localhost is harder to protect (D147). Size (~120 MB) is small next to the ~6 GB of models. Reverses D124's "not a desktop app": that choice was about Phase 8's scope |
| D143 | Docker Desktop is required; the app runs the stack as a Compose project of its own (name, free loopback ports, data folder), from images built in CI and pinned by digest, with a local build as the fallback | Bundle a native Postgres + pgvector, Redis and Python; SQLite + sqlite-vec | User's choice (2026-10-16). The stack and its tests stay exactly as measured, and packaging stays packaging, not a port to new runtimes. Cost, said plainly on the download page: Docker Desktop is a large install, may need WSL2 and a restart on Windows, and has its own license terms. Pinned digests mean an install runs exactly the images CI tested |
| D144 | Background jobs stay on Redis + Celery | A job queue in Postgres (SKIP LOCKED, LISTEN/NOTIFY) | User's choice (2026-10-16). With Docker, Redis is one more container; the job code (chains, retries, time limits, late acks, D88–D91, D107, D131) is tested and measured, and rewriting it would add risk without a desktop benefit |
| D145 | The desktop app learns about PRs and pushes by polling GitHub (conditional requests with ETags, every 30–60 s per installation, plus a catch-up on start); webhooks through a relay stay available as a setting; both feed one shared intake | smee only (v1); both offered in the wizard | User's choice (2026-10-16). Polling needs no third party (smee and anyone with its URL see every delivery, D137) and loses nothing while the machine sleeps, where webhooks to a stopped machine are lost. A shared intake means allowlist, cap, supersede and dedupe rules can't drift between the two. Cost: a review may start up to a minute later |
| D146 | Use the Ollama the user has; if there is none, download Ollama's official installer, check its signature and run it with the user's consent. The 16k-window settings are an opt-in button | Run a private copy of Ollama on its own port | User's choice (2026-10-16). One Ollama per machine: no second copy of the models on disk, no two servers fighting over VRAM, and the user's own settings stay theirs. The default stays the 8k window, which works with no server settings (D66) |
| D147 | `LOCAL_MODE`: the desktop dashboard has no sign-in and acts as the machine's one owner (an admin, as the demo acts as one anonymous visitor, D117); its web server answers only requests with a loopback Host carrying the app's key, a cookie only the app's window holds; the secrets are generated per install | GitHub sign-in as on the web (D113) | User's choice (2026-10-16). The app is single-user; a sign-in would add a browser round trip on every start and a redirect URI to configure. Loopback alone isn't enough, because any local program or web page can reach 127.0.0.1 (the lesson of D132); the key and the Host check close that |
| D148 | The first release is Windows x64 only, unsigned; macOS and Linux later | Windows signed from the start; Windows and macOS together | User's choice (2026-10-16). Windows is where everything was measured (the RTX 4060). Unsigned is free, and SmartScreen's warning can be explained; signing can be added later without other changes. macOS can't ship unsigned (Gatekeeper), so it needs the paid Apple account and a Mac to test on |
| D149 | The dashboard joins Docker Compose as a `web` container (Next.js `output: 'standalone'`), used by the desktop app and available to the self-hosted setup | Electron runs the Next server itself | One image set for every product, the same way the API and workers are shipped, and it closes v1's gap that the dashboard ran only with `pnpm dev` (architecture.md §15). Electron then only supervises and displays |
| D150 | Everything the installer downloads is checked before use: Docker Desktop's and Ollama's installers by their Windows signature (the expected publisher), CodeLens images by pinned digest, models by Ollama's own digests; nothing runs from a "latest" URL unchecked | Trust the HTTPS download | The security posture v1 kept (hash-pinned grammars, D31; secrets owner-only, D132) carries to the installer: a hijacked download or a swapped image must fail, not run. Publisher installers change with every version, so their signature is what can be pinned |
| D151 | Updates come from the project's public GitHub Releases: checked on start (can be turned off), applied only after the user agrees, with a database backup before new migrations run | Silent auto-update; no updates | A silent update would run new code on someone's machine without asking, against D139. With no updates at all, security fixes never reach users. The backup makes a bad migration recoverable, since migrations are never edited once applied (D29) |

---

## 8. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Docker Desktop is too much for non-developers (WSL2, a restart, its size, its license) | High | High | The wizard explains each step before it happens and resumes after the restart; the download page states the requirement up front. If the rehearsal shows it's the main obstacle, revisit D143 |
| Docker Desktop's startup bug on Windows Insider builds (`planning.md` §1.5) | Medium | Medium | The supervisor detects "Docker installed, not running" and shows the documented fix |
| Unsigned installer flagged by SmartScreen or antivirus (an app that starts other processes) | High | Medium | The download page shows the "Run anyway" path; signing is the first item under "Later" |
| GitHub rate limits from polling many repos | Low | Medium | Conditional requests; one installation-level repo list; the interval grows with repo count; measured in step 2 (Q22) |
| GPU shared with games or too small; CPU-only machines | High | Medium | The wizard reports the GPU before the 6 GB download; reviews still run on CPU, slowly; a smaller model is a question for the evals (Q23) |
| The user's Ollama on another port, or a version without structured outputs | Medium | Medium | The wizard finds and checks it (version and `/api/version`), and Settings lets the URL change (Q24) |
| Docker Desktop alternatives (Rancher Desktop, Podman) | Medium | Low | Detected and called unsupported in v2 (Q25) |
| A Next.js standalone build that misses files in the monorepo | Medium | Low | The spike (Phase 11 step 1) builds and runs it; `outputFileTracingRoot` as the Next docs describe |
| The brand pass grows into a redesign of everything | Medium | Medium | Step 1 fixes tokens and primitives only; each later step owns its pages |

---

## 9. Open questions

| # | Question | Decide by |
|---|---|---|
| Q21 | Which brand: accent color, logo mark, type? | Phase 10 step 1 (2–3 options shown) |
| Q22 | Polling interval and how it scales with the number of repos; does GitHub count 304 answers against the rate limit for App installation tokens? | Phase 11 step 2 (measure) |
| Q23 | Offer a smaller model (e.g. `qwen3:4b`) for GPUs under 8 GB? It would need its own eval run before it's offered, since the F1 0.767 is `qwen3:8b`'s | After Phase 11, with the eval harness |
| Q24 | Support an Ollama on another machine (a home server)? | Phase 11 step 6 |
| Q25 | Support Rancher Desktop or Podman instead of Docker Desktop? | After the rehearsal |
| Q26 | Should "start at login" default to on? | Phase 11 step 4 |

---

## 10. Conventions and definition of done

Unchanged from v1 (`planning.md` §19), with v2's numbering:

- **Branches:** `phase-N/step-M-short-name`; conventional commits; one PR per step; stop
  for review after each.
- **Docs:** this file's status line and §6 checkmarks are updated as steps land; new
  decisions are added to §7 with their alternatives and reasons; `architecture.md` changes
  in the same PR as the components it describes.
- **Code:** the dashboard's `AGENTS.md` applies: Next.js 16 differs from older versions, so
  read the guide in `node_modules/next/dist/docs/` before writing Next code.
- **A step is done when** the code and tests are merged, CI is green, docs are updated,
  there's a "what I built / how to run it / what to verify" note, and the user has
  reviewed it.

**v2 is done when:**

1. The dashboard runs on the new design system and brand everywhere: the demo, the
   self-hosted web and the desktop app.
2. A Windows user with nothing installed goes from the download page to a review on their
   own PR by their own App, through the wizard alone.
3. Nothing in that path touches the owner's laptop, App, relay or services (§3).
4. The README tells both stories: try the demo on the web, or download the software.
