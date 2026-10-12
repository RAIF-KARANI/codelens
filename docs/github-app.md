# Setting up the CodeLens GitHub App

CodeLens talks to GitHub as a **GitHub App**: a bot identity with exactly the
permissions it needs, installed on the repositories it reviews. This guide creates
the App for local development, receives its webhooks on your laptop, and checks
that everything is connected.

You need: the stack running (`docker compose up -d postgres redis`, migrations
applied), Node (for the webhook tunnel), and a GitHub account.

## 1. A tunnel for webhooks

GitHub sends events (a PR was opened, a push landed) to a public URL, and your laptop
isn't public. [smee.io](https://smee.io) relays them: GitHub posts to a smee channel,
and a small client on your machine forwards each delivery to the API.

1. Open <https://smee.io/new>. The page's address is your channel URL, e.g.
   `https://smee.io/AbC123xyz`. Keep it.
2. Leave this running in its own terminal (the API listens on port 8000):

```bash
npx smee-client --url https://smee.io/AbC123xyz --target http://localhost:8000/webhooks/github
```

> Why not `gh webhook forward`? It forwards repository and organisation webhooks
> only; a GitHub App's webhook is configured on the App itself (D94).

## 2. Create the App from its manifest

The App's permissions and events are defined in code
(`packages/core/src/core/github/manifest.py`), so you don't fill in GitHub's form
by hand:

| Permission | Access | Why |
|---|---|---|
| Contents | read | Clone and index the code |
| Metadata | read | Required by GitHub for every App |
| Pull requests | read & write | Read PRs, post review comments |

It subscribes to `push` and `pull_request` events (`installation` events always
come).

```bash
uv run python -m core.github register --name "CodeLens (your-login)" --webhook-url https://smee.io/AbC123xyz
```

- **Your browser opens a local page;** press **Continue to GitHub**. GitHub shows the
  App's name and permissions, and you create it.
- **GitHub redirects back to the command,** which saves the credentials into the
  gitignored `secrets/` folder:
  - `secrets/github-app.pem`: the App's private key;
  - `secrets/github-app.env`: `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH`,
    `GITHUB_WEBHOOK_SECRET`, plus an OAuth client id and secret for the Phase 6
    dashboard.
- **App names are global on GitHub,** hence your login in the name. Add `--org my-org`
  to create the App under an organisation.

Copy the lines of `secrets/github-app.env` into `.env`. Never commit either file; both
are gitignored.

## 3. Install it on a test repository

Open the install link the command printed (`https://github.com/apps/<slug>/installations/new`),
choose **Only select repositories**, and pick a test repository.

## 4. Check the connection

```bash
uv run python -m core.github check
```

It walks the whole chain, signing an App JWT, reading the App, and minting a token for
each installation. Expected output:

```
App        CodeLens (your-login) (https://github.com/apps/codelens-your-login)
  permissions {'contents': 'read', 'metadata': 'read', 'pull_requests': 'write'}  (expected ...)
  events      ['pull_request', 'push']  (expected ...)
  webhook secret in .env: set

1 installation(s)
  your-login (User), id 12345678, repos: selected: installation token OK
```

Then start the API with the new settings (`uv run uvicorn api.main:app --port 8000`).
The installation you just made shows up when its event is delivered: GitHub's App
settings page → **Advanced** → **Recent deliveries** lists every delivery, and
**Redeliver** sends one again. You can also check the database:

```bash
docker compose exec postgres psql -U codelens -c "SELECT github_installation_id, account_login FROM installations"
```

## What the webhook endpoint does (Phase 5 steps 2–3a)

`POST /webhooks/github`:

1. **Refuses** everything if `GITHUB_WEBHOOK_SECRET` is unset (503), and in `DEMO_MODE`
   (403).
2. **Verifies the signature:** `X-Hub-Signature-256` must be the HMAC-SHA256 of the raw
   body under the webhook secret, compared in constant time. Otherwise 401, and the
   body is never parsed.
3. **Records the delivery** by its `X-GitHub-Delivery` id. A delivery already handled
   is answered `duplicate` and skipped; one that failed halfway is handled again (D95).
4. **Mirrors `installation` events** (created, suspend, unsuspend, deleted,
   new_permissions_accepted) into the `installations` table.
5. **Attaches repositories:** installing the App, or adding repos to an installation,
   creates a repo row for each and queues **index → embed**. The worker keeps a bare
   mirror of each repo in `data/repos/<id>.git` and indexes its default branch. Removed
   repos are detached (their data stays).
6. **Re-indexes on push** to a repo's default branch. Pushes to other branches are
   recorded as `ignored`.
7. **Records `pull_request` events** as `ignored` for now; step 3b reviews them.

So after installing the App on a test repo (with the API, both workers and the smee
client running), `GET /api/v1/repos` lists the repo, and its index run appears under
`/api/v1/repos/{id}/index-runs` within a minute or so.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Deliveries show **401** in GitHub's "Recent deliveries" | `GITHUB_WEBHOOK_SECRET` in `.env` doesn't match the App's secret (restart the API after editing `.env`) |
| **503 webhook_not_configured** | `GITHUB_WEBHOOK_SECRET` isn't set |
| Nothing arrives | The smee client isn't running, or `--target` points at the wrong port |
| `check`: **401 Bad credentials** / "A JSON web token could not be decoded" | Wrong `GITHUB_APP_ID` or private key, or your clock is off by minutes |
| `check`: private key not found | `GITHUB_APP_PRIVATE_KEY_PATH` is relative to where you run the command: run it from the repo root |
| `register` times out | The browser page wasn't completed within 15 minutes; run it again (nothing was created) |
