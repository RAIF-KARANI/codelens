# The public demo

The demo is a read-only copy of CodeLens anyone can open, with no login (D117): the
reviews of one **public** demo repo, with their diffs, findings, evidence, call graph
and retrieval trace. Reviews are made on the laptop (it has the GPU) and then copied to
the demo's database; the hosted demo has no model, worker or Redis (planning.md §15).

| | Laptop | Hosted demo |
|---|---|---|
| Makes reviews | yes: GitHub App, workers, Ollama | no |
| Database | everything you've indexed | only the exported demo repos |
| API | normal | `DEMO_MODE=true`: read-only, Postgres only, every repo visible to every visitor (D115) |
| Dashboard | sign in with GitHub | `DEMO_MODE=true`: no sign-in; everyone is the same anonymous visitor (D117) |

The API still requires a token on a demo: only the dashboard, which holds
`API_JWT_SECRET`, can call it. A request straight to the API gets a 401, and any write a
403.

## The demo repository

[`RAIF-KARANI/codelens-demo`](https://github.com/RAIF-KARANI/codelens-demo): a public copy
of [sqlite-utils](https://github.com/simonw/sqlite-utils) (Apache-2.0, credited in its
README; its own CI workflows are left out) with three pull requests, reviewed by the App
from the laptop on 2026-10-13:

| PR | What it carries | CodeLens found |
|---|---|---|
| #1 Simplify the trigger lookup query | a planted SQL injection (the table name formatted into the query) | 1 critical, inline; 15 s |
| #2 Clarify a variable name in reset_counts | nothing: a harmless rename | no findings; 8 s |
| #3 Cleanup pass across the library | four planted bugs: swapped parameters, a removed guard, `None` instead of an exception, the wrong exception type | all four, inline; 23 s |

The export of it is `data/demo/codelens-demo.json.gz` (555 KB; `data/` is gitignored).

## 1. Make the reviews (laptop)

1. Create the **public** demo repo on GitHub and add it to the App's installation
   (`docs/github-app.md` §3). CodeLens mirrors and indexes it.
2. Open pull requests on it. Each push is reviewed and the review posted on the PR, as
   in `docs/e2e.md`. Check the reviews in the local dashboard.

## 2. Export them

```bash
uv run python -m core.demo export --repo you/codelens-demo --out demo.json.gz
```

It writes the repo's rows (repo, files, code graph, index runs, reviews, findings, trace,
timeline) to one compressed file, and leaves out embeddings, 👍/👎 votes, installations
and local paths. **It refuses any repo that isn't public on GitHub** (it asks GitHub
without credentials, so a private repo looks missing), and any local repo: every demo
visitor sees every repo there.

## 3. Import them into the demo's database

The demo's database must be migrated to the same revision as the laptop's:

```bash
DATABASE_URL=<the demo database's asyncpg URL> uv run alembic -c packages/core/alembic.ini upgrade head
uv run python -m core.demo import demo.json.gz --database-url <the demo database's asyncpg URL>
```

An import **replaces** the repos it contains (deleting their old rows first), so
re-exporting after new reviews and importing again updates the demo. Ids are kept, so
nothing else should write to the demo's database.

## 4. Run the demo

| Service | Settings |
|---|---|
| API | `DEMO_MODE=true`, `DATABASE_URL` (the demo's database), `API_JWT_SECRET` |
| Dashboard | `DEMO_MODE=true`, `API_URL` (the API's address), `API_JWT_SECRET` (the same value) |

No GitHub, session or Redis settings are needed.

## 5. Host it: Supabase + Vercel + your subdomain (D116)

The demo runs at **`https://codelens.raifkarani.in`**: the dashboard and the API on
Vercel, the database on Supabase. Put the database and the API in the **same region**
(here Mumbai): every page makes several database queries.

### Supabase: the database

1. Create a project (e.g. `codelens-demo`), region **South Asia (Mumbai)**. Keep the
   database password in your password manager.
2. **Connect** → **Session pooler** (port 5432; the direct address is IPv6-only, which
   Vercel can't reach). Turn its URI into an asyncpg URL:
   `postgresql+asyncpg://postgres.<ref>:<password>@aws-0-ap-south-1.pooler.supabase.com:5432/postgres?ssl=require`
3. Put it in `.env.demo` at the repo root (gitignored), next to the demo's own token
   secret:

   ```
   DEMO_DATABASE_URL=postgresql+asyncpg://...
   DEMO_API_JWT_SECRET=<at least 32 random bytes>
   ```

4. Load the demo into it (migrations, then the export; an import replaces those repos,
   so this is also how to refresh the demo after new reviews):

   ```bash
   python scripts/publish_demo.py
   ```

### Vercel: two projects from this repository

| | API | Dashboard |
|---|---|---|
| Project name | `codelens-api` | `codelens` |
| Root Directory | `deploy/api` (`deploy/api/README.md`) | `apps/web` |
| Framework | FastAPI (detected) | Next.js (detected) |
| `DEMO_MODE` | `true` | `true` |
| `DATABASE_URL` | the value of `DEMO_DATABASE_URL` | — |
| `API_URL` | — | `https://codelens-api.vercel.app` (the API project's address) |
| `API_JWT_SECRET` | the value of `DEMO_API_JWT_SECRET` | the same value |
| Function region | Mumbai (`bom1`) | Mumbai (`bom1`) |

Every push to `main` redeploys both. The API project's daily cron (`deploy/api/vercel.json`)
calls `/health`, which keeps the free Supabase project from pausing after a week idle.

### Cloudflare: the subdomain

1. In the dashboard project on Vercel: **Settings → Domains → Add**
   `codelens.raifkarani.in`.
2. In Cloudflare, DNS for `raifkarani.in`: add a **CNAME** record, name `codelens`, target
   the value Vercel shows (e.g. `cname.vercel-dns.com`), proxy status **DNS only** (grey
   cloud), so Vercel can issue the HTTPS certificate.
3. Vercel shows the domain as valid within minutes.

### Check it

- `https://codelens.raifkarani.in` opens the repos without a sign-in, with the demo
  banner, and `RAIF-KARANI/codelens-demo` is the only repo.
- A review's page, graph and trace work; the timeline replays.
- `https://codelens-api.vercel.app/health` is ok with `"redis": null`;
  `https://codelens-api.vercel.app/api/v1/repos` answers 401 (no token).
