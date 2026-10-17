# End-to-end tests

Playwright drives the dashboard built for production, against the API in demo
mode over the demo export (`fixtures/codelens-demo.json.gz`: the same three
reviews of `RAIF-KARANI/codelens-demo` the live demo shows). CI runs them on
every push (`.github/workflows/ci.yml`, job `e2e`).

| Spec | Checks |
|---|---|
| `smoke.spec.ts` | Every page loads with its data; filters, j/k, the graph's Enter and Escape, the trace's sorting, the command palette, the theme, a 404 |
| `a11y.spec.ts` | axe's WCAG 2.2 A and AA rules on every page in both themes; a visible focus on everything Tab reaches; reduced motion stops animations |
| `layout.spec.ts` | On desktop, tablet and phone: no page scrolls sideways, the navigation is reachable, tap targets are big enough (WCAG 2.5.8) |
| `visual.spec.ts` | The main pages compared with stored screenshots (`__screenshots__/`) |
| `readme.shots.ts` | Not a test: writes the README's images (`docs/images`) when asked |

## Running them

Playwright starts both servers itself, so all it needs is a database with the
export loaded. From the repo root, with the Compose stack's Postgres up:

```bash
docker compose exec postgres createdb -U codelens codelens_e2e
uv run python scripts/e2e_data.py apps/web/e2e/fixtures/codelens-demo.json.gz --database-url postgresql+asyncpg://codelens:<password>@localhost:5433/codelens_e2e
```

Then from `apps/web`:

```bash
npx playwright install chromium
E2E_DATABASE_URL=postgresql+asyncpg://codelens:<password>@localhost:5433/codelens_e2e pnpm e2e
```

`E2E_PYTHON` says how to run Python with the repo's packages if `uv run python`
isn't it (on Windows, the quoted path to `.venv\Scripts\python.exe`).

## Screenshots

Fonts render differently on Windows, macOS and Linux, so the stored
screenshots come from Linux, in the image CI runs in, and `visual.spec.ts`
skips itself anywhere else. To compare or update them from another OS, run the
browser in that image and point Playwright at it:

```bash
docker run --rm -d --name pw -p 3555:3555 --add-host=host.docker.internal:host-gateway mcr.microsoft.com/playwright:v1.63.0-noble npx -y playwright@1.63.0 run-server --port 3555 --host 0.0.0.0
E2E_BROWSER_WS=ws://localhost:3555/ pnpm e2e visual --update-snapshots
docker stop pw
```

The image's version must match `@playwright/test` in `package.json` (and the
`container:` in CI). A changed screenshot is reviewed like code: the PR shows
the old and new images.

## The README's images

```bash
README_SHOTS=1 pnpm e2e --project=readme
```

These are taken with your own browser, not the one in Docker: headless
Chromium on Linux spaces letters unevenly, which a test baseline doesn't mind
but a README does.

## When a migration lands

Nothing to do: `scripts/e2e_data.py` loads the export at the schema revision it
was made at, then migrates to head, so the tests also run the demo's data
through every migration since.
