# The demo video: a script

> A short **launch film** (24 s, no narration) is already in the README:
> `docs/media/codelens-launch.mp4`. This script is for the longer, narrated walkthrough.

A 2½–3 minute screen recording for the README and for applications: a real pull request
reviewed live, the dashboard explaining the review, and the evaluation story. The
narration below is a guide, not a text to read word for word; say it in your own words.

## Before recording

- [ ] **Close games and anything else using the GPU** (`nvidia-smi` should show it near
      0% with CodeLens idle). With VALORANT open, a review took 3 minutes instead of 20 s.
- [ ] Plugged in, Windows power mode on best performance.
- [ ] The stack running: `docker compose up -d`, the smee relay, the dashboard
      (`pnpm --dir apps/web dev`), signed in. `curl localhost:8000/health` says `"ok"`.
- [ ] **Warm the model** with one throwaway review, so the recorded one isn't the slow
      first load.
- [ ] **A prepared branch** on `RAIF-KARANI/codelens-demo` with one planted bug that reads
      naturally, e.g. a function that now returns `None` where it used to raise, used by
      a caller that doesn't check. Don't open the PR yet.
- [ ] Browser at 100–110% zoom, one window, notifications off (Windows: Focus).
- [ ] Recorder: OBS (or the Xbox Game Bar, Win+Alt+R) at 1920×1080, 30 fps, with a
      microphone level check.

## Scenes

| # | Time | On screen | Say |
|---|---|---|---|
| 1 | 0:00–0:12 | `codelens-demo` PR #1 on GitHub, scrolled to the bot's inline comment (the SQL injection) | "This is CodeLens, an AI code reviewer I built. It reviews pull requests like this one, on my own GPU, and every finding points at the line and the code behind it." |
| 2 | 0:12–0:35 | Open the prepared branch's PR on GitHub. Switch to the dashboard: the repo's Reviews list, the new review `queued`, then `running` | "When I open a pull request, GitHub sends a webhook to my laptop. The API queues a job, and a worker reviews the change with a local model: qwen3, 8 billion parameters, through Ollama. Nothing leaves the machine." |
| 3 | 0:35–1:00 | The review's page: the **Timeline** filling in live, then the diff with the finding on its line | "Each step streams in live: it finds the changed functions, retrieves related code from the repository by meaning, packs it into the model's context window, and validates what comes back. A finding must sit on a changed line and cite code the model was actually shown, otherwise it's dropped." |
| 4 | 1:00–1:10 | Back on GitHub: the new inline comment on the PR | "And here it is on GitHub, about twenty seconds after I opened the PR." |
| 5 | 1:10–1:40 | The finding's **Evidence** (the cited code opens), **Show in graph** (the graph highlights the changed function and its evidence), then the **Trace** tab | "Every review can be explained. This is the code the finding cites; this is the call graph around the change; and the trace shows every candidate retrieval found, whether the model saw it, and why not." |
| 6 | 1:40–2:20 | `evals/RESULTS.md` on GitHub (or the README's results table) | "Before improving anything, I built an evaluation set: 83 cases from four open-source repos, planted bugs, safe twins of those bugs, and clean refactors. An upgrade only ships if its gain holds up statistically. Anchoring did: it raised F1 from 0.65 to 0.77. Four other ideas, including the model's thinking mode, failed the test and didn't ship. And one honest result: at 8 billion parameters, the retrieved context doesn't pay for itself yet." |
| 7 | 2:20–2:40 | The README's architecture diagram | "Under the hood: FastAPI and Celery workers, Postgres with pgvector, tree-sitter for the code graph, and a Next.js dashboard. The webhooks are signed and idempotent, and the App only works for accounts I allow." |
| 8 | 2:40–2:55 | `codelens.raifkarani.in` in the browser | "There's a read-only version online at codelens.raifkarani.in, and the code, the plan and every measurement are on GitHub. Thanks for watching." |

## After recording

- Trim the waiting in scene 2 (keep a second of `queued` → `running`).
- Add the link to the README, under the live demo line: `**Video:** [3-minute
  walkthrough](<link>)`. YouTube (unlisted) or a GitHub release asset both work.
- Close the prepared PR, or keep it as a fourth demo PR: then re-export and publish the
  demo (`python scripts/publish_demo.py`, [`docs/demo.md`](demo.md)) so the online
  version shows it too.
