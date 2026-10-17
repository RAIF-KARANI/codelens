# CodeLens dashboard

The web front end of CodeLens: Next.js 16 (App Router, Server Components, Cache
Components), React 19, Tailwind 4 and shadcn/ui. It reads everything from the CodeLens
API on the server; the browser never holds an API token. How it fits into the rest of
the system is in [`architecture.md`](../../architecture.md) §10.3, and the setup in
[`docs/development.md`](../../docs/development.md#dashboard).

## Run it

```bash
pnpm install
cp .env.example .env.local   # then fill it in: the API's URL and secret, the GitHub App's OAuth client
pnpm dev                     # http://localhost:3000
```

It needs the API running. With `DEMO_MODE=true` (and the API in demo mode too) nobody
signs in, every visitor sees the demo's repos read-only, and Re-index and 👍/👎 are
hidden ([`docs/demo.md`](../../docs/demo.md)).

## Checks

```bash
pnpm lint
pnpm test    # vitest: the server code (sessions, tokens, OAuth, diffs, layout maths)
pnpm build
pnpm e2e     # Playwright against the demo export: e2e/README.md
```

CI runs all four on every pull request.

## Where things are

| Path | What |
|---|---|
| `src/app/` | The pages: `/` overview, `/reviews`, `/repos`, `/repos/[id]` (reviews, indexing), `/reviews/[id]` (diff, graph, trace), `/login`, plus `error.tsx` and `not-found.tsx` |
| `src/app/api/` | Route handlers: GitHub sign-in, the review event stream, code nodes, the command palette's data. Each checks the session |
| `src/components/` | Shared pieces: the shell (sidebar, top bar, ⌘K palette), tables, stat tiles, the empty and error state (`state-message.tsx`) |
| `src/components/ui/` | shadcn/ui's components, copied in (D153: the CLI isn't a dependency) |
| `src/lib/` | The API client (`api.ts`, `dal.ts`), sessions and tokens, diff parsing and highlighting, graph layout, theme and sidebar state, colour contrast |
| `src/app/globals.css` | The Aperture brand: every colour as a token, light and dark |
| `test/` | vitest |
| `e2e/` | Playwright: smoke, accessibility (axe), layout, screenshots |

Before changing framework code, read the guide for this Next.js version in
`node_modules/next/dist/docs/` (`AGENTS.md`): it differs from older versions.
