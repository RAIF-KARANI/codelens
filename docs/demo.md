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

No GitHub, session or Redis settings are needed. Hosting it (Vercel + Supabase, D116) is
Phase 7 step 3.
