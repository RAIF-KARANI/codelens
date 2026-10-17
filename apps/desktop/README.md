# CodeLens Desktop

CodeLens as a Windows app ([`planning-v2.md`](../../planning-v2.md), Phase 11): it runs
the whole CodeLens stack on the user's machine, as a Docker Compose project of its own,
and shows the dashboard in its window, with no sign-in.

**Status: Phase 11 step 5b.** On its first run a wizard checks the computer; finds,
starts or installs Docker Desktop and Ollama (from their publishers, signature checked);
pulls the models; creates your own GitHub App in your browser and opens its install page;
and checks it all with `codelens doctor --desktop --json`. Then the app starts the stack
(and its control API, for the dashboard's Settings page),
shows the dashboard, keeps it all up (a supervisor every 10 s), lives in the tray, and
stops the stack when it quits; `pnpm dist` builds an unsigned installer. Settings,
updates and releases come in later steps.

| File | What |
|---|---|
| `compose.yaml` | The Compose project `codelens-desktop`: Postgres, Redis, migrations, the API, both workers and the dashboard (`web`), from published images pinned by digest. Only the dashboard has a port, on loopback |
| `compose.build.yaml` | The fallback: build CodeLens's three images from this checkout instead |
| `images.json` | The published images, pinned by digest (from the `Images` workflow's summary) |
| `src/stack.ts` | The stack as functions: the data folder and its secrets, a free port, the launch's key, `docker compose up / stop / ps`, image and memory checks |
| `src/main.ts` | The Electron app: start the stack, give the window the launch's key, show the dashboard, stop on quit |
| `src/window.ts` | What the window may show (this launch's dashboard only) and open (https links, in your browser); the status pages and their buttons |
| `src/supervisor.ts` | What to start or restart, the backoff, what to tell the user |
| `src/firstrun.ts`, `src/wizard.ts` | The first-run wizard's flow and its screen |
| `src/machine.ts`, `src/installers.ts` | The computer's checks; downloading, checking and running Docker's and Ollama's installers |
| `src/control.ts`, `src/appsettings.ts`, `src/ollamaenv.ts` | The control API the Settings page calls; the settings it may change; the 16k window |
| `src/models.ts`, `src/githubapp.ts` | Pulling the models; creating your GitHub App (in the `tools` container) and the stack's own checks |
| `src/logs.ts`, `src/settings.ts` | Each service's log file; `desktop.json` |
| `src/cli.ts` | `pnpm stack …`: the stack from a terminal |

## What runs where

- **Data folder:** `%LOCALAPPDATA%\CodeLens\` (or `CODELENS_HOME`): `codelens.env`
  with the secrets this install generated (`POSTGRES_PASSWORD`, `API_JWT_SECRET`),
  `data/` (the repos' mirrors), `secrets/` (the GitHub App's key), `logs/`
  (`desktop.log`, `compose.log`). The database is the Docker volume
  `codelens-desktop_pgdata`.
- **The dashboard** is the `web` container in local mode (D147, D165): no sign-in; the
  API treats every caller as the machine's owner. It answers only requests that name a
  loopback host and carry the app's key, a cookie the app sets in its own window. A new
  key is made at every launch, kept in memory and passed to the container, never written
  to disk. A browser tab on the same machine has no key and gets a 403.
- **The API, Postgres and Redis** have no published ports.
- **Ollama** is the user's own, on the host (`host.docker.internal:11434`).

## Run it

Docker Desktop running; Node 22 and pnpm.

```bash
pnpm install
pnpm start                   # build, then open the app (pulls the pinned images the first time)
CODELENS_LOCAL_IMAGES=1 pnpm start   # or with images built from this checkout
pnpm dist                    # release/CodeLens-Setup-<version>.exe, unsigned
```

If `pnpm install` leaves no `node_modules/electron/dist`, run
`node node_modules/electron/install.js` once (it downloads Electron and checks it against
the package's checksums).

`CODELENS_MEASURE=1` logs the spike's numbers to `logs/desktop.log` (time to a running
stack, idle memory a minute later) and quits.

The installer is unsigned (D148): Windows SmartScreen says "Windows protected your PC";
**More info → Run anyway**. It installs for the current user only, under
`%LOCALAPPDATA%\Programs\`, and removes itself from **Settings → Apps**; the data folder
and the Docker volume stay until removed by hand (the uninstaller will ask, step 7).

## Day to day

| | |
|---|---|
| The tray icon | **Open CodeLens**; the status (Running, Paused, Waiting for Docker Desktop, Ollama isn't running); **Pause reviews** / **Resume reviews** (stops the workers and the poller; the dashboard stays); **Start when Windows starts**; **Open data folder**; **Quit CodeLens** |
| Closing the window | CodeLens keeps running in the tray; Quit stops it and the stack |
| Docker Desktop stops | A waiting screen; CodeLens starts again by itself when Docker answers |
| A service dies | The supervisor starts it again within about 10 s (with backoff if it keeps failing) |
| Ollama stops | A Windows notice; reviews fail, and say so on the PR, until it's back |
| What went wrong | `logs/desktop.log` (the app), `logs/compose.log` (starting), `logs/services/<service>.log` (each service) |

## The stack from a terminal

```bash
pnpm build
pnpm stack init              # the data folder and its secrets (safe to run again)
pnpm stack up                # start from the pinned images, wait until healthy
pnpm stack up --local        # or build the images from this checkout first
pnpm stack status
pnpm stack stop              # stops the containers; the data stays
```

`up` prints the dashboard's address and, for development only, the launch's key, with
a `curl` line that sends it.

To remove everything: `docker compose -p codelens-desktop down -v` (with the variables
from `pnpm stack env` set), then delete the data folder.

## Tests

```bash
pnpm test    # vitest: the data folder, secrets, image pins, compose arguments, ports, the window's guard
```
