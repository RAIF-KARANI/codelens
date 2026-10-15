# Run your own CodeLens

CodeLens reviews your GitHub pull requests on **your own machine**, with **your own GitHub
App**: your code is cloned to your computer, reviewed by a model running on it, and the
comments are posted on your pull requests. Nothing goes to anyone else's server.

This guide takes about 20 minutes, most of it downloads.

## What you need

| | |
|---|---|
| **A GPU** | An NVIDIA card with 8 GB of video memory (or a Mac with Apple silicon) reviews a pull request in under a minute. Without one it works, but takes a few minutes per review. Close games while it reviews: they share the GPU |
| **[Docker Desktop](https://docs.docker.com/get-docker/)** | Runs the database, the API, the workers and the webhook relay |
| **[Ollama](https://ollama.com)** | Runs the models (about 6 GB of downloads, done for you) |
| **[uv](https://docs.astral.sh/uv/getting-started/installation/)** and **[Git](https://git-scm.com/downloads)** | To get the code and run its commands |
| **A GitHub account** | Your App is created on it |

Start Docker Desktop and Ollama before the next step.

## Two commands

```bash
git clone https://github.com/RAIF-KARANI/codelens && cd codelens
uv sync
uv run codelens doctor
uv run codelens setup
```

**`codelens doctor`** checks what your machine has and prints a to-do list. It changes
nothing, so run it whenever something seems off. If it says to install something (Docker,
Ollama), do that first; the rest is the last line: `uv run codelens setup`.

**`codelens setup`** does the rest, and asks you twice:

1. **"Your GitHub username"**, then a name for your App (the suggested one is fine; App
   names are unique across GitHub).
2. **Your browser opens:** click **Continue to GitHub**, then **Create GitHub App**. GitHub
   sends you back by itself. The App asks for the least it needs: to read code, and to
   write pull request reviews.
3. If the models aren't there yet: **"Pull … with Ollama now?"**, answer `y`.

Then it starts everything (the first time builds the containers, a few minutes) and prints
your App's **install link**.

## Install the App, open a pull request

Open the install link, choose **Only select repositories**, pick the repos you want
reviewed, and click **Install**. Each repo is copied to your machine and indexed (a few
minutes for a small repo, longer for a big one).

Then open a pull request, or push to one, that changes **Python, JavaScript or
TypeScript**. A review from **`<your-app>[bot]`** appears on it, usually within a minute
or two: a summary, and comments on the lines it found problems with. Pushing again
reviews the new code without repeating what it already said.

## Day to day

| | |
|---|---|
| Start it again (after a restart) | Start Docker Desktop and Ollama, then `uv run codelens setup` (it only does what's needed) |
| Stop it | `docker compose --profile relay stop` |
| Something's wrong | `uv run codelens doctor` |
| Watch a review happen | `docker compose logs -f worker-gpu` |
| More repos | Your App's settings on GitHub → **Install App** → configure |

Reviews only happen while it's running: GitHub's events for a stopped machine are lost. To
review a pull request opened in the meantime, push to it again.

## Good to know

- **Your code stays on your machine.** The repos are cloned into `data/repos/`, the code
  graph and reviews live in the local database, the model runs in Ollama. GitHub only
  receives the review comments.
- **Your App only works for you.** It acts only for the GitHub accounts in
  `GITHUB_ALLOWED_ACCOUNTS` in `.env` (setup puts yours there). To let someone else use your
  machine for their repos, add their login, run `docker compose restart api`, and make the App
  public in its settings (see [`github-app.md`](github-app.md) §6).
- **Limits:** at most 10 reviews per account per hour (`GITHUB_REVIEWS_PER_HOUR`); files in
  other languages are skipped (a pull request with only those still gets a summary).
- **Secrets** are in `.env` and `secrets/`, which git ignores. Don't share them.

## Removing it

`docker compose --profile relay down -v` removes the containers and the database; delete the
`data/` folder for the copied repos. Then delete the App on GitHub: **Settings → Developer
settings → GitHub Apps → your App → Advanced → Delete GitHub App**.

## When something goes wrong

| What you see | What to do |
|---|---|
| `codelens setup` stops with a message | It says what to do; do it and run setup again |
| doctor: `Docker … installed, but not running` | Start Docker Desktop |
| doctor: `Ollama … not reachable` | Start Ollama |
| doctor: `GitHub App … isn't installed on any repo yet` | Open the install link it prints |
| No review appears | `uv run codelens doctor`; then `docker compose logs api` (did the event arrive?) and `docker compose logs worker-gpu` (did the review run?) |
| Reviews take minutes | Something else is using the GPU (a game), or the machine has no supported GPU |

The full reference is [`github-app.md`](github-app.md) (the App, step by step) and
[`e2e.md`](e2e.md) (running and watching the whole system).
