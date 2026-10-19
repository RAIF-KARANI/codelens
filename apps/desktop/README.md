# CodeLens Desktop

> Using it rather than building it? The user's guide is [`docs/desktop.md`](../../docs/desktop.md).

CodeLens as a Windows app ([`planning-v2.md`](../../planning-v2.md), Phase 11): it runs
the whole CodeLens stack on the user's machine, as a Docker Compose project of its own,
and shows the dashboard in its window, with no sign-in.

**Status: Phase 11 step 7b.** On its first run a wizard checks the computer; finds,
starts or installs Docker Desktop and Ollama (from their publishers, signature checked);
pulls the models; creates your own GitHub App in your browser and opens its install page;
and checks it all with `codelens doctor --desktop --json`. Then the app starts the stack
(and its control API, for the dashboard's Settings page),
shows the dashboard, keeps it all up (a supervisor every 10 s), lives in the tray, and
stops the stack when it quits; the dashboard has Settings and System pages. A version
tag publishes the installer to GitHub Releases; the app looks for a newer one and
installs it when you say so; uninstalling asks about the data.

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
| `src/control.ts`, `src/appsettings.ts`, `src/ollamaenv.ts` | The control API the Settings and System pages call; the settings it may change; the 16k window |
| `src/system.ts` | What the System page shows: sizes, the end of a log, the services it may name |
| `src/models.ts`, `src/githubapp.ts` | Pulling the models; creating your GitHub App (in the `tools` container) and the stack's own checks |
| `src/logs.ts`, `src/settings.ts` | Each service's log file; `desktop.json` |
| `src/updates.ts`, `src/backup.ts` | Updates, and the database backup before a new version's migrations |
| `src/uninstall.ts`, `installer/uninstall.nsh` | What uninstalling does besides removing the files |
| `src/releasenotes.ts`, `scripts/release-notes.mjs` | A release's notes and checksum |
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
`%LOCALAPPDATA%\Programs\`, and removes itself from **Settings → Apps**.

**Uninstalling** first runs the app once more (`CodeLens.exe --uninstall`,
`src/uninstall.ts`): start at login off and the stack's containers removed; then it
asks whether to keep the data. **Delete everything** removes the database volume, then
the data folder, then this version's images, both or neither (Postgres keeps its first
password, so a volume left behind locks out the next install), and offers your GitHub
App's page to delete it there. A silent uninstall (`/S`) keeps the data.

## Releasing

Set `version` in `package.json`, merge, then tag `main`:

```bash
git tag v0.2.0
git push origin v0.2.0
```

`.github/workflows/release.yml` checks the tag matches the version and is on `main`,
and that every image in `images.json` is published; builds the installer on Windows;
and publishes it with `latest.yml`, `SHA256SUMS.txt` and notes (`src/releasenotes.ts`:
the download, SmartScreen, the checksum, what the computer needs, the images).

## Updates

On start and every day (unless switched off in the tray or on the Settings page),
CodeLens asks GitHub for the latest release's `latest.yml`. A newer one is offered, never
taken: **Update** downloads it (checked against its SHA-512), then **Restart now** stops
the stack and installs it; the new version starts by itself.

Its first start checks whether its migrations change the database. If they do, the
database is copied first to `backups/codelens-<old version>-<time>.dump` (the last three
are kept), and nothing migrates without that copy. To go back: install the older version
from Releases, then, with CodeLens running,

```bash
docker exec -i codelens-desktop-postgres-1 pg_restore -U codelens -d codelens --clean --if-exists < codelens-0.1.0-20261018T120000Z.dump
```

To try an update without a release: build two versions with `publish` set to
`{ "provider": "generic", "url": "http://127.0.0.1:8765/" }` (and
`extraMetadata.version`), serve the newer one's folder on that port, and install the older.

## Day to day

| | |
|---|---|
| The tray icon | **Open CodeLens**; the status (Running, Paused, Waiting for Docker Desktop, Ollama isn't running); **Pause reviews** / **Resume reviews** (stops the workers and the poller; the dashboard stays); **Start when Windows starts**; **Open data folder**; **Update to …** when there is one; **Check for updates now**; **Check for updates automatically**; **Quit CodeLens** |
| Closing the window | CodeLens keeps running in the tray; Quit stops it and the stack |
| Docker Desktop stops | A waiting screen; CodeLens starts again by itself when Docker answers |
| A service dies | The supervisor starts it again within about 10 s (with backoff if it keeps failing) |
| Ollama stops | A Windows notice. A review waits for it, queued, up to 10 minutes; after that it fails and says so on the PR, and it's reviewed again by itself once Ollama answers. A failed review's page also has **Review again** |
| What went wrong | The dashboard's **System** page (each service, its health and its log), or the files: `logs/desktop.log` (the app), `logs/compose.log` (starting), `logs/services/<service>.log` (each service) |

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
pnpm test    # vitest: the data folder, secrets, image pins, compose arguments, ports, the window's guard, the wizard, Settings, System, uninstalling, release notes
```
