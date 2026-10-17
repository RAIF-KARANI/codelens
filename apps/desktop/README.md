# CodeLens Desktop

CodeLens as a Windows app ([`planning-v2.md`](../../planning-v2.md), Phase 11): it runs
the whole CodeLens stack on the user's machine, as a Docker Compose project of its own,
and shows the dashboard in its window, with no sign-in.

**Status: Phase 11 step 1a.** This folder holds the stack the app runs and a
development CLI that drives it. The Electron window, the tray and the installer
come next (step 1b and later).

| File | What |
|---|---|
| `compose.yaml` | The Compose project `codelens-desktop`: Postgres, Redis, migrations, the API, both workers and the dashboard (`web`), from published images pinned by digest. Only the dashboard has a port, on loopback |
| `compose.build.yaml` | The fallback: build CodeLens's three images from this checkout instead |
| `images.json` | The published images' digests (the `Images` workflow's summary). Empty until the first publish from `main` |
| `src/stack.ts` | The stack as functions: the data folder and its secrets, a free port, the launch's key, `docker compose up / stop / ps` |
| `src/cli.ts` | `pnpm stack …`: the same, from a terminal |

## What runs where

- **Data folder:** `%LOCALAPPDATA%\CodeLens\` (or `CODELENS_HOME`): `codelens.env`
  with the secrets this install generated (`POSTGRES_PASSWORD`, `API_JWT_SECRET`),
  `data/` (the repos' mirrors), `secrets/` (the GitHub App's key), `logs/`. The
  database is the Docker volume `codelens-desktop_pgdata`.
- **The dashboard** is the `web` container in local mode (D147): no sign-in; the API
  treats every caller as the machine's owner. It answers only requests that name a
  loopback host and carry the app's key, a cookie the app's window sets. A new key
  is made at every launch, kept in memory and passed to the container, never written
  to disk. A browser tab on the same machine has no key and gets a 403.
- **The API, Postgres and Redis** have no published ports: only the containers on the
  project's network reach them.
- **Ollama** is the user's own, on the host (`host.docker.internal:11434`).

## Run it from a terminal

Docker Desktop running; Node 22 and pnpm.

```bash
pnpm install
pnpm build
pnpm stack init              # the data folder and its secrets (safe to run again)
pnpm stack up --local        # build the images from this checkout, start, wait until healthy
pnpm stack status --local
pnpm stack stop --local      # stops the containers; the data stays
```

`up` prints the dashboard's address and, for development only, the launch's key, with
a `curl` line that sends it. Without `--local`, `up` pulls the images pinned in
`images.json`.

To remove everything: `docker compose -p codelens-desktop down -v` (with the variables
from `pnpm stack env --local` set), then delete the data folder.

## Tests

```bash
pnpm test    # vitest: the data folder, secrets, image pins, compose arguments, ports
```
