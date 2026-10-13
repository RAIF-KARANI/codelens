# Running CodeLens end to end

The whole system on one machine: GitHub → webhook → API → queue → workers → local models
→ a review posted on the pull request, with its progress streamed live. This is the run
that closed Phase 5 (`planning.md` §9); the measurements are in §6.4 there.

## What runs where

| Piece | Where | Started by |
|---|---|---|
| Postgres, Redis, migrations, API (:8000), `worker` (queue `index`), `worker-gpu` (queue `gpu`) | Docker | `docker compose up -d` |
| Ollama (`qwen3:8b`, `qwen3-embedding:0.6b`) | natively on the host (GPU) | the Ollama app |
| The webhook relay | the host | `npx smee-client …` |

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

## Run

```bash
docker compose up --build -d
docker compose ps                      # api healthy; worker, worker-gpu up
npx smee-client --url https://smee.io/<channel> --target http://localhost:8000/webhooks/github
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

```bash
docker compose logs -f worker-gpu      # reviews and embedding slices
TOKEN=$(docker compose exec -T api python -m api.token)
curl -N -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/repos/<repo_id>/reviews
curl -N -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/reviews/<review_id>/events
```

The last command streams the review's events live (`stage`, `chunk_done`, `done`,
`published`, `end`) and returns when the review ends.

## When something's off

| Symptom | Look at |
|---|---|
| Nothing happens on GitHub events | the smee client is running; the App's settings → *Advanced* → *Recent deliveries* (a 401 there: `GITHUB_WEBHOOK_SECRET` doesn't match; restart `api` after editing `.env`) |
| A repo never appears | the delivery's status in *Recent deliveries*; `docker compose logs api` |
| Indexing fails with a git error | the installation still covers the repo; `docker compose logs worker` |
| Reviews time out | Ollama is up and reachable from a container: `docker compose exec worker-gpu python -c "import httpx; print(httpx.get('http://host.docker.internal:11434/api/version').text)"` |
| A review is posted much later than expected | an embedding job ran first: the `gpu` queue is FIFO, but embedding gives way every slice (D107) |

To stop: `docker compose down` (data stays in the `pgdata` volume and `./data`).
