# Future upgrade: CodeLens as a hosted service

**Status: not planned for Phase 7.** This is the design note for a later upgrade, written
down so the work is known when someone (say, a friend with his own repo) wants to use
CodeLens without running it themselves.

**The goal:** anyone installs the CodeLens GitHub App on their repos; their pull requests
get reviewed by an always-on server, with comments on GitHub; and they sign in on the
CodeLens website (e.g. `codelens.<your-domain>`) to see **their own** reviews, with the
diff, evidence, graph and trace.

## How it differs from today

| | Today (Phases 1–7) | Hosted service |
|---|---|---|
| Who can install the App | Only its owner (`public: false`); Phase 7 step 4 adds an allowlist so a few chosen accounts can too | Anyone, or anyone on a waitlist/allowlist |
| Where reviews are made | The owner's laptop: Ollama on its GPU, Docker workers | Servers that are always on |
| How GitHub reaches it | smee.io relay to the laptop | The hosted API's public URL |
| The website | The public demo: read-only, one public repo, no login (D117) | Each user signs in with GitHub and sees only their installations' repos (D111) |
| Whose code is stored | The owner's repos, on the laptop | Strangers' code, possibly private, on hosted servers |
| Cost | Free (the laptop already exists) | A monthly bill: model, workers, Redis, database, storage |

## What already exists and carries over

- **Isolation between users (D111).** Every API route checks that the caller's token
  lists the repo's App installation. A user sees only their own repos; anything else is
  a 404. This is the core of multi-user safety, and it's tested.
- **GitHub login (D113)** puts the user's installations in the session at sign-in.
- **Installation events (D97, D101):** repos attach and detach as people install and
  change the App; pushes re-index; PR events queue reviews; newer pushes supersede older
  reviews (D103).
- **A complete review record (D110):** the website never needs git access.
- **Never executing repo code:** repos are read as git objects only; git runs without a
  shell or external diff tools (architecture.md §13.1).
- **Queues kept apart (D88):** indexing doesn't block reviews.

## What has to be built

1. **A model that isn't a laptop GPU.** Either:
   - **a hosted, OpenAI-compatible model API.** Simplest; it needs a new provider
     (`LLM_PROVIDER=openai_compatible`, planned in planning.md §15 but not built; only
     `ollama` and `fake` exist). The embedder needs one too. Users' code is then sent to
     that provider, which the privacy notice must say; or
   - **a rented GPU running Ollama.** Code stays on our servers; costs more, always on.

   Either way the eval harness should re-score the chosen model before launch: the
   reported F1 (0.767) is qwen3:8b's.
2. **Always-on workers and Redis.** The `worker` and `worker-gpu` containers on a
   container host, plus a managed Redis. Mirrors (`DATA_DIR/repos/`) need a disk that
   survives restarts, or are re-cloned when missing.
3. **The full API deployed, not the demo:** `DEMO_MODE=false`, webhooks pointed at its
   URL (replacing smee), the job queue enabled.
4. **Opening the App:** "Any account" in its settings, behind an allowlist or a waitlist
   at first (the Phase 7 step 4 allowlist is the starting point).
5. **Limits per installation,** so one user can't use up the budget: a maximum repo size
   to index, reviews per day, tokens per review, and a queue position that's fair across
   installations rather than FIFO.
6. **Data lifecycle.** Today an uninstall *detaches* repos but keeps their rows and
   mirror. A service must **delete** a repo's code, graph, embeddings, reviews and mirror
   when it's removed, and say how long data is kept.
7. **Abuse and safety:** rate limits (Phase 7 step 4 adds the first), resource limits on
   parsing huge or hostile repos (file size and count caps, timeouts), and alerts when
   queues or costs grow.
8. **The website for many users:** an onboarding page ("Install the App" → "Sign in"),
   and installation status shown on the repos page.
9. **Terms and a privacy notice:** what's stored, where the code goes (especially with a
   hosted model API), retention, and how to delete everything.

## Decisions to make first

| Question | Options |
|---|---|
| Where does the model run? | Hosted model API (cheaper to start, code leaves our servers) · rented GPU (private, fixed cost) |
| Who may install? | An allowlist of friends · a waitlist · anyone |
| Which limits? | Repo size, reviews per day, tokens per review, private repos allowed or not |
| How long is data kept? | Until uninstall · N days after the last review |
| Free or paid? | Free for a few users with hard limits · paid plans (billing is a project of its own) |

## A possible order

1. **Pilot, cheapest:** keep the laptop as the reviewer, open the App to an allowlist
   (Phase 7 step 4). Friends get reviews on their PRs while the laptop is on. (Their code
   comes to the laptop, which they should agree to.)
2. **Hosted model:** add the OpenAI-compatible provider; re-run the evals; measure cost
   per review.
3. **Hosted workers:** workers and Redis on a container host; the API's webhooks move
   off smee; the laptop is no longer needed.
4. **Website for users:** sign-in shows each user their own repos (already true in
   non-demo mode), plus onboarding and limits.
5. **Open up:** a waitlist, the privacy notice, deletion on uninstall, cost alerts.

Prices for model APIs, GPUs and hosting change often, so estimate costs when this starts,
from the measured tokens per review (`reviews.token_usage`; about 4,700 prompt and 550
output tokens for a typical playground PR).
