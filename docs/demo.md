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
| API | `DEMO_MODE=true`, `DATABASE_URL` (the demo's database), `API_JWT_SECRET`; behind a transaction-mode pooler also `DATABASE_TRANSACTION_POOLER=true` |
| Dashboard | `DEMO_MODE=true`, `API_URL` (the API's address), `API_JWT_SECRET` (the same value) |

No GitHub, session or Redis settings are needed.

## 5. Host it: Supabase + Vercel + your subdomain (D116)

The demo runs at **`https://codelens.raifkarani.in`**: the dashboard and the API on
Vercel, the database on Supabase. Put the database and the API in the **same region**
(here Mumbai): every page makes several database queries.

### Supabase: the database

1. Create a project (e.g. `codelens-demo`), region **South Asia (Mumbai)**. Keep the
   database password in your password manager.
2. **Connect** → **Session pooler** (port 5432; the direct address
   `db.<ref>.supabase.co` is IPv6-only, which Vercel can't reach). Turn its URI into an
   asyncpg URL; note the user is `postgres.<ref>`, not `postgres`:
   `postgresql+asyncpg://postgres.<ref>:<password>@aws-0-ap-south-1.pooler.supabase.com:5432/postgres?ssl=require`
   (for the demo project `yisrqwbbadkcdnyttisl`, `aws-0-ap-south-1` answers;
   `aws-1-ap-south-1` says the tenant doesn't exist).
3. Put it in `.env.demo` at the repo root (gitignored), next to the demo's own token
   secret, and the same address on port **6543** (the **Transaction pooler**) for the
   hosted API:

   ```
   DEMO_DATABASE_URL=postgresql+asyncpg://...:5432/postgres?ssl=require
   DEMO_API_DATABASE_URL=postgresql+asyncpg://...:6543/postgres?ssl=require
   DEMO_API_JWT_SECRET=<at least 32 random bytes>
   ```

   **Why two poolers.** The session pooler gives each client its own server connection
   and allows only 15 at once (`pool_size` on the free plan). Every Vercel instance keeps
   its own few connections and a review page makes ~8 calls at once, so the hosted API
   ran out: `(EMAXCONNSESSION) max clients reached in session mode` in Supabase's logs,
   and a plain-text 500 for the call. The transaction pooler lends a server connection
   per transaction, so many clients share a few. It can't keep prepared statements
   between transactions, which `DATABASE_TRANSACTION_POOLER=true` handles (asyncpg's
   statement caches off, unique statement names; without it, ~1 call in 5 failed with
   `prepared statement "__asyncpg_stmt_8__" does not exist`). Migrations and
   `publish_demo.py` keep the session pooler (`DEMO_DATABASE_URL`): DDL and the
   import's long transactions want one connection throughout.

4. Load the demo into it (migrations, then the export; an import replaces those repos,
   so this is also how to refresh the demo after new reviews):

   ```bash
   python scripts/publish_demo.py
   ```

**Row-level security.** Supabase also serves every `public` table through its own REST
API, to anyone holding the project's public (anon) key. New Supabase projects switch
row-level security on for every new table, and with no policies that API can read and
write nothing, while our API, connecting as the tables' owner, is unaffected. Check after
loading, in Supabase's SQL editor:

```sql
select relname, relrowsecurity from pg_class
where relnamespace = 'public'::regnamespace and relkind = 'r';  -- all true
select count(*) from pg_policies where schemaname = 'public';      -- 0
```

If a table shows `false`: `alter table <name> enable row level security;`.

**Changing the database password** (do it whenever it may have leaked): reset it in
Supabase (**Settings → Database**), then put the new one in `.env.demo`'s
`DEMO_DATABASE_URL` and `DEMO_API_DATABASE_URL` (and `SUPABASE_DIRECT_URL`, if kept)
and in the API project's `DATABASE_URL` on Vercel, and redeploy that project.

**Done on 2026-10-15:** the project `codelens-demo` (`yisrqwbbadkcdnyttisl`, Mumbai,
Postgres 17) is migrated to `cd53acf29082` and holds `RAIF-KARANI/codelens-demo` only
(3 reviews, 5 findings); row-level security on for all 14 tables, no policies. Run
locally against it, `deploy/api`'s `main:app` served `/health`, the repos, a review, its
diff, trace, graph and events (0.4–0.9 s per call from the laptop), refused a missing
token and one signed with the laptop's secret (401).

### Vercel: two projects from this repository

| | API | Dashboard |
|---|---|---|
| Project name | `codelens-api` | `codelens` |
| Root Directory | `deploy/api` (`deploy/api/README.md`) | `apps/web` |
| Framework | FastAPI (detected) | Next.js (detected) |
| `DEMO_MODE` | `true` | `true` |
| `DATABASE_URL` | the value of `DEMO_API_DATABASE_URL`: the **Transaction pooler** address (`postgres.<ref>@aws-0-…pooler.supabase.com:6543`), with `postgresql+asyncpg://` and `?ssl=require`. Not the direct `db.<ref>.supabase.co` one (Vercel has no IPv6, so it times out), nor the session pooler's 5432 (15 connections in all) | — |
| `DATABASE_TRANSACTION_POOLER` | `true` | — |
| `API_URL` | — | the API project's production address, without a trailing slash: ours is `https://codelens-api-beta.vercel.app` (`codelens-api.vercel.app` belongs to someone else) |
| `API_JWT_SECRET` | the value of `DEMO_API_JWT_SECRET` | the same value |
| Function region | Mumbai (`bom1`), set by `deploy/api/vercel.json` | Mumbai (`bom1`), set by `apps/web/vercel.json` |

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
- `https://codelens-api-beta.vercel.app/health` is ok with `"redis": null`;
  `https://codelens-api-beta.vercel.app/api/v1/repos` answers 401 (no token).
- Its responses carry `X-Vercel-Id: bom1::bom1::…`: the second part is where the function
  ran. `bom1::iad1::…` means Washington, and every database round trip then crosses the
  world (a new connection took 1.2 s instead of about 0.1 s).

**Live since 2026-10-15** (projects `codelens-api` and `codelens`, both Mumbai): all of
the above checked on the live site, plus 6 bursts of 16 parallel API calls (a review
page's calls, twice), 96/96 OK. If a page shows an error, the dashboard project's
**Logs** say which call failed; `Error: HTTP 500` there is the API crashing, so look at
the API project's logs and Supabase's (**Logs → Pooler**).
