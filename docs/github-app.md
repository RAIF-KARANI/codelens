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

> **The quick way:** `uv run codelens setup` does sections 1–4 for you (a smee channel,
> the App, `.env`, the models, the stack with the relay) and `uv run codelens doctor`
> checks the result. The sections below are what it does, step by step.

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
  App's name and permissions, and you create it. The link carries a one-time token and
  works in that browser only: if no browser opens, open the printed link yourself, once.
- **GitHub redirects back to the command,** which saves the credentials into the
  gitignored `secrets/` folder:
  - `secrets/github-app.pem`: the App's private key;
  - `secrets/github-app.env`: `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH`,
    `GITHUB_WEBHOOK_SECRET`, `GITHUB_ALLOWED_ACCOUNTS` (you, the App's owner; see §6),
    plus an OAuth client id and secret for the Phase 6 dashboard.

  Both files are readable by you only (0600, in a 0700 folder) from the moment they
  exist; so is the `.env` that `codelens setup` writes.
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

## 5. Let the dashboard sign users in

The dashboard (`apps/web`) signs users in with the App's own OAuth client, so GitHub must
know where to send them back. Apps registered after Phase 6 step 2 have this already;
for an older App:

1. Open the App's settings: `https://github.com/settings/apps/<slug>` (for an organisation's
   App, the organisation's **Settings → Developer settings → GitHub Apps**).
2. Under **Identifying and authorizing users**, type
   `http://localhost:3000/api/auth/callback` into the **Redirect URI** box (older GitHub
   pages call it **Callback URL**; **Add redirect URI** adds another box once one is
   used). Leave **Allow wildcard matching**, **Request user authorization (OAuth) during
   installation** and **Enable Device Flow** unticked.
3. Scroll to the bottom of the page and press **Save changes**.

Then copy `apps/web/.env.example` to `apps/web/.env.local` and fill it in: the client id
and secret are the `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` from `.env`, and
`API_JWT_SECRET` must match the API's. On **Sign in with GitHub**, GitHub asks you once to
authorize the App; you then see the repos of every installation you can access. Logins
in `API_ADMIN_LOGINS` also see local and eval repos.

## 6. Let a friend use it on their repos (D119, D120)

The App is created **"Only on this account"**, so nobody else can install it. Opening it
to someone (Phase 7 step 4a) is safe because CodeLens only acts on installations by the
accounts in **`GITHUB_ALLOWED_ACCOUNTS`**:

1. **List the accounts** in `.env`, comma-separated GitHub logins (users or
   organisations, any case), yourself included:
   `GITHUB_ALLOWED_ACCOUNTS=your-login,friends-login`. Unset means **nobody**: it fails
   closed like the secrets, so a missing line can never open your GPU to strangers.
   Restart the API.
2. **Open the App:** its settings page (`https://github.com/settings/apps/<slug>`) →
   **Advanced** → **Make this GitHub App public** → confirm. ("Public" only means others
   may install it; it gets no access to anything they don't grant.)
3. **Send your friend the install link** (`https://github.com/apps/<slug>/installations/new`).
   They choose **Only select repositories** and pick theirs. Their repos are indexed,
   and their PRs reviewed, by your laptop while the API, workers and smee client run.
   They see the reviews on their PRs; your local dashboard shows their repos to you if
   you're in `API_ADMIN_LOGINS`.

Anyone else who installs it is **ignored**: the delivery is logged as `ignored`, the
API log says `installation … by … ignored: not in GITHUB_ALLOWED_ACCOUNTS`, and nothing
is stored, indexed or reviewed. Taking an account off the list stops its pushes and PRs
at the next event; its uninstall is still applied.

**Review cap.** Each account may start **`GITHUB_REVIEWS_PER_HOUR`** reviews an hour
(default 10; 0 = no cap). Reviews that ran or are waiting count; pushes that replace a
queued review of the same PR don't add one. Over the cap, the review is recorded as
**failed** with the reason (the dashboard shows it), not run; the next push, or
reopening the PR, reviews that commit once the hour has room.

## What the webhook endpoint does (Phase 5 steps 2–3b)

`POST /webhooks/github`:

1. **Refuses** everything if `GITHUB_WEBHOOK_SECRET` is unset (503), and in `DEMO_MODE`
   (403).
2. **Verifies the signature:** `X-Hub-Signature-256` must be the HMAC-SHA256 of the raw
   body under the webhook secret, compared in constant time. Otherwise 401, and the
   body is never parsed.
3. **Records the delivery** by its `X-GitHub-Delivery` id. A delivery already handled
   is answered `duplicate` and skipped; one that failed halfway is handled again (D95).
4. **Mirrors `installation` events** (created, suspend, unsuspend, deleted,
   new_permissions_accepted) into the `installations` table, for accounts in
   `GITHUB_ALLOWED_ACCOUNTS` only (§6); others are recorded as `ignored`.
5. **Attaches repositories:** installing the App, or adding repos to an installation,
   creates a repo row for each and queues **index → embed**. The worker keeps a bare
   mirror of each repo in `data/repos/<id>.git` and indexes its default branch. Removed
   repos are detached (their data stays).
6. **Re-indexes on push** to a repo's default branch. Pushes to other branches are
   recorded as `ignored`.
7. **Reviews pull requests:** opening a PR (or pushing to it, reopening it, or marking
   a draft ready) queues a review of its head. The worker fetches the PR into the
   mirror, reviews it with the local model, and posts **one comment review**: inline
   comments, plus a summary even when there's nothing new.
   - A push made while an older one waits or runs supersedes it.
   - Comments on a function that's unchanged since an earlier push aren't repeated.
   - Drafts wait until they're marked ready.

So after installing the App on a test repo (with the API, both workers and the smee
client running):
- `GET /api/v1/repos` lists the repo, and its index run appears under
  `/api/v1/repos/{id}/index-runs` within a minute or so.
- Opening a pull request that changes Python, JavaScript or TypeScript code gets a review
  from `<your-app>[bot]`, typically within a minute on an 8 GB GPU.
- Files in other languages are not reviewed; a PR touching only those still gets a summary.

On Windows without a global `uv`, run the commands with the repo's interpreter instead:
`.venv\Scripts\python.exe -m core.github register ...`. The worker needs
`--pool=solo` on Windows.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Deliveries show **401** in GitHub's "Recent deliveries" | `GITHUB_WEBHOOK_SECRET` in `.env` doesn't match the App's secret (restart the API after editing `.env`) |
| **503 webhook_not_configured** | `GITHUB_WEBHOOK_SECRET` isn't set |
| Deliveries say `"status": "ignored"` for your own installation, PRs and pushes; the API log says `not in GITHUB_ALLOWED_ACCOUNTS` | Your login isn't in `GITHUB_ALLOWED_ACCOUNTS` (unset means nobody). Add it, restart the API, and **Redeliver** the installation event from the App's "Recent deliveries" |
| A review shows **failed: not reviewed: … the limit (GITHUB_REVIEWS_PER_HOUR=…)** | That account started that many reviews in the last hour; push again or reopen the PR later |
| Nothing arrives | The smee client isn't running, or `--target` points at the wrong port |
| `check`: **401 Bad credentials** / "A JSON web token could not be decoded" | Wrong `GITHUB_APP_ID` or private key, or your clock is off by minutes |
| `check`: private key not found | `GITHUB_APP_PRIVATE_KEY_PATH` is relative to where you run the command: run it from the repo root |
| `register` times out | The browser page wasn't completed within 15 minutes; run it again (nothing was created) |
| The local page says **Refused** | It was opened without the printed link, or by another browser than the one that opened it first: use the printed link |
| `register` stops: **opened by another client first** | The printed link was opened twice by different clients (another program, or another user on this machine); the run stops so nothing it started is saved. Run it again |
