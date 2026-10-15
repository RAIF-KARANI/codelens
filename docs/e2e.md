# Running CodeLens end to end

The whole system on one machine: GitHub → webhook → API → queue → workers → local models
→ a review posted on the pull request, with its progress streamed live, and the dashboard
to watch it in. This is the run that closed Phase 5 and, with the dashboard, Phase 6
(`planning.md` §9); the measurements are in §6.4 there.

## What runs where

| Piece | Where | Started by |
|---|---|---|
| Postgres, Redis, migrations, API (:8000), `worker` (queue `index`), `worker-gpu` (queue `gpu`) | Docker | `docker compose up -d` |
| Ollama (`qwen3:8b`, `qwen3-embedding:0.6b`) | natively on the host (GPU) | the Ollama app |
| The webhook relay | Docker (the `smee` service), or the host | `docker compose --profile relay up -d` (with `SMEE_URL` in `.env`), or `npx smee-client …` |
| The dashboard (:3000) | the host | `pnpm dev` in `apps/web` |

Containers reach Ollama at `http://host.docker.internal:11434`. Compose mounts `./data`
(the repos' bare mirrors) and `./secrets` (the App's private key, read-only) into both
workers.

## One-time setup

1. `.env` from `.env.example`, with:
   - `API_JWT_SECRET`: at least 32 bytes;
   - the GitHub App's settings (`docs/github-app.md`: `register` writes them to
     `secrets/github-app.env`).
2. Pull the models: `ollama pull qwen3:8b` and `ollama pull qwen3-embedding:0.6b`.
3. A smee channel (https://smee.io/new) set as the App's webhook URL. `register` sets it
   when it creates the App.
4. For the dashboard: `apps/web/.env.local` from `apps/web/.env.example`, and
   `http://localhost:3000/api/auth/callback` as the App's redirect URI
   (`docs/github-app.md` §5).

## Run

```bash
docker compose up --build -d
docker compose ps                      # api healthy; worker, worker-gpu up
curl localhost:8000/health             # "ok"; "degraded" = Ollama is down or lacks a model
npx smee-client --url https://smee.io/<channel> --target http://localhost:8000/webhooks/github
# or, with SMEE_URL in .env, the relay as a container (what `codelens setup` does):
# docker compose --profile relay up -d
pnpm --dir apps/web dev                # the dashboard, http://localhost:3000
```

Then, on GitHub:
- **Install the App on a repo** (or add a repo to the installation): the repo appears
  in `GET /api/v1/repos`, is mirrored into `data/repos/<id>.git`, indexed, and embedded in
  slices of 512 nodes.
- **Open a pull request** that changes Python, JavaScript or TypeScript: a review from
  `<your-app>[bot]` follows, usually within a minute or two.
- **Push to the PR again:** a new review, without repeating comments on code that didn't
  change.

## Watch it

**In the dashboard** (http://localhost:3000, signed in with GitHub):

1. **Repositories** → the repo → its **Reviews**. A new push's review appears as
   `queued`, titled like the PR; the list re-renders itself while it runs.
2. **Open the review.** Its **Timeline** fills in live: changed definitions found,
   context packed, the chunk reviewed (with its time), validated, posted on GitHub. At
   the end the page re-renders with the diff and the findings on their lines.
3. **A finding's Evidence** button opens the code it cites; **Show in graph** opens
   the **Graph** tab with the finding's changed code and evidence highlighted, among
   its callers and callees.
4. The **Trace** tab shows, per changed definition, every candidate retrieval found and
   whether the model saw it, and why not.

**From the command line:**

```bash
docker compose logs -f worker-gpu      # reviews and embedding slices
TOKEN=$(docker compose exec -T api python -m api.token)
curl -N -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/repos/<repo_id>/reviews
curl -N -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/reviews/<review_id>/events
```

The last command streams the review's events live (`stage`, `chunk_done`, `done`,
`published`, `end`) and returns when the review ends.

**Logs.** Each request is one `api.access` line (method, path, status, `duration_ms`),
and every line it causes carries its `request_id`, also answered as `X-Request-ID`.
With `LOG_FORMAT=json` in `.env` the lines are JSON, and a job's lines carry its
`task_id`, so one review's lines can be pulled out:
`docker compose logs worker-gpu | grep <task id>`.

## When something's off

| Symptom | Look at |
|---|---|
| Nothing happens on GitHub events | the smee client is running; the App's settings → *Advanced* → *Recent deliveries* (a 401 there: `GITHUB_WEBHOOK_SECRET` doesn't match; restart `api` after editing `.env`) |
| A repo never appears | the delivery's status in *Recent deliveries*; `docker compose logs api` |
| Indexing fails with a git error | the installation still covers the repo; `docker compose logs worker` |
| `/health` says `degraded` | `checks.ollama.error`: `ProviderUnavailableError` = Ollama isn't running or reachable; `ModelNotPulled` = `ollama pull` the model named in the API log |
| Reviews are far slower than usual | something else is using the GPU (a game): `nvidia-smi` with CodeLens idle should show it near 0% |
| Reviews time out | Ollama is up and reachable from a container: `docker compose exec worker-gpu python -c "import httpx; print(httpx.get('http://host.docker.internal:11434/api/version').text)"` |
| The dashboard says "Something went wrong" with `invalid_token` | `API_JWT_SECRET` in `apps/web/.env.local` must equal the API's |
| The dashboard shows no repos | sign in again (installations are read at sign-in); the App is installed on the repo; your login is in `API_ADMIN_LOGINS` for local/eval repos |
| A review is posted much later than expected | an embedding job ran first: the `gpu` queue is FIFO, but embedding gives way every slice (D107) |

To stop: `docker compose down` (data stays in the `pgdata` volume and `./data`).
