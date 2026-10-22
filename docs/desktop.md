# CodeLens Desktop: the user's guide

CodeLens Desktop reviews the pull requests on your GitHub repositories with an AI model
that runs on your own Windows PC, and posts what it finds as comments on the pull request.
This guide takes you from the download to your first review, then covers day-to-day use,
what to do when something goes wrong, and how to remove it.

Your code goes between GitHub and your PC, and nowhere else: the model runs on your
computer, and the GitHub App CodeLens creates is yours alone. Nothing of the project's
owner is involved.

## What you need

- **Windows 10 (22H2) or Windows 11, 64-bit**, with **virtualization on** (Docker Desktop
  runs a small Linux VM). Most PCs have it on; the setup says if yours doesn't.
- **8 GB of memory** at least; 16 GB is comfortable.
- **15 GB free on the disk** at least; 30 GB is comfortable. Docker Desktop, CodeLens and
  the models take about 12 GB.
- **An NVIDIA GPU with 6 GB or more** is best: a review then takes under a minute.
  Without one, reviews run on the CPU, in a few minutes each.
- **A GitHub account**, and repositories in Python, JavaScript or TypeScript to review.
- About **30 minutes** the first time, most of it downloads.
- **A PC that is yours.** CodeLens trusts whatever answers on this PC's Ollama port
  (127.0.0.1:11434), as every Ollama client does: Ollama has no password. On a PC shared
  with other Windows accounts, another account could start its own program there while
  your Ollama is off, and receive your code with each review. The dashboard itself is
  protected with a key that changes whenever its port is let go.

## 1. Download and install

1. Go to **[codelens.raifkarani.in](https://codelens.raifkarani.in)** and choose
   **Download CodeLens for Windows** (about 106 MB).
2. Run `CodeLens-Setup-<version>.exe`. Windows says **"Windows protected your PC"**:
   CodeLens isn't code-signed yet. Choose **More info**, then **Run anyway**.
3. It installs for you only (no administrator rights) and opens CodeLens.

To check the file is exactly the one GitHub built: in PowerShell, run
`Get-FileHash .\CodeLens-Setup-<version>.exe` in your Downloads folder and compare the
SHA-256 with the one on the download page or in the release's notes.

## 2. The setup

CodeLens's window walks you through six steps. Each one looks before it acts, so if you
close the window halfway, or Windows restarts, it picks up where it left off.

| Step | What happens | What you do |
|---|---|---|
| **Check this computer** | Windows version, virtualization, memory, disk, GPU | Fix anything marked red; yellow is a warning, and setup carries on |
| **Docker Desktop** | Found and started, or downloaded from Docker and installed (its signature checked first) | Follow Docker's own installer. It may ask to restart Windows: CodeLens opens again by itself afterwards |
| **Ollama** | Found and started, or downloaded from Ollama and installed, the same way | Nothing, usually |
| **Models** | `qwen3:8b` (reviews) and `qwen3-embedding:0.6b` (finding related code) are downloaded into Ollama, about 6 GB, with a progress bar | Wait |
| **Your GitHub App** | Your browser opens GitHub with an App ready to create, named after you | Sign in to GitHub if asked, then **Create GitHub App**. Back in CodeLens, choose **Install your App**, pick the repositories to review, and **Install** |
| **Start CodeLens** | Everything checked once more, then the dashboard opens | When asked, choose whether CodeLens starts when you sign in to Windows |

## 3. Your first review

Open a pull request on one of the repositories you installed the App on, or push a new
commit to an open one. Within a minute or so:

- CodeLens notices it (it asks GitHub every 60 seconds; no webhook or relay is needed);
- the first time, it indexes the repository: its code graph and search index;
- the review runs on your GPU, and a **CodeLens review** appears on the pull request:
  a summary, and inline comments on the changed lines, each with a severity
  (🔴 critical, 🟠 warning, 🔵 suggestion), what's wrong, and often a suggested fix.

Each later push is reviewed too, and a finding already posted isn't posted again.
A clean change gets a short "no new findings" review.

**About the findings:** they come from an 8-billion-parameter model on your PC. It finds
real bugs well, but it can rate one more or less severe than you would, or miss a
subtle one. Every finding shows the code it was based on, so you can judge it.

## 4. The dashboard

The CodeLens window is the dashboard:

| Page | What's there |
|---|---|
| **Overview** | The last 7 days: reviews, findings by severity, how long reviews take, the latest reviews |
| **Reviews** | Every review, filterable by status. A review's page shows the diff with its findings, the live timeline, the **Graph** (the code the model was shown, and what each finding cites) and the **Trace** (everything retrieval found) |
| **Repositories** | Your repositories, their index, and **Re-index** |
| **System** | Each service's health, with Restart; Docker and Ollama; disk use; each service's log |
| **Settings** | Who the App reviews for and how many reviews an hour, how often CodeLens asks GitHub, the models and their context window, and **Updates** |

## 5. Day to day

CodeLens lives in the **tray**, near the clock. Closing the window doesn't stop reviews.

| Tray item | What it does |
|---|---|
| **Open CodeLens** | Shows the window |
| *The status* | Running, Paused, Waiting for Docker Desktop, Ollama isn't running, or a download's progress |
| **Pause reviews** / **Resume reviews** | Stops and starts reviewing; the dashboard stays. Pushes made while paused are reviewed after you resume |
| **Start when Windows starts** | On or off |
| **Open data folder** | `%LOCALAPPDATA%\CodeLens`: settings, logs, database backups |
| **Update to …**, **Check for updates now**, **Check for updates automatically** | See below |
| **Quit CodeLens** | Stops CodeLens and its services. Pushes made meanwhile are reviewed when you open it again |

**Updates:** CodeLens looks for a new release when it starts and once a day (you can turn
that off in the tray or on the Settings page). It never installs one on its own: choose
**Update**, then **Restart now**, and it installs the update and starts again in about a
minute. If the new version changes the database, it saves a copy first, in the data
folder's `backups` (the last three are kept).

## 6. When something goes wrong

| What you see | What to do |
|---|---|
| **"Windows protected your PC"** | Expected: **More info**, then **Run anyway** |
| **"Docker Desktop isn't running"** | Choose **Start Docker Desktop**, or start it yourself. CodeLens carries on by itself once Docker answers |
| **Docker Desktop won't start** (some Windows Insider builds) | Quit it, rename `%LOCALAPPDATA%\Docker\run` to something else, and start it again |
| **"Ollama isn't running"** in the tray | Start Ollama from the Start menu. Reviews wait for it, queued, up to 10 minutes. A review that failed meanwhile is reviewed again by itself once Ollama answers |
| **"CodeLens couldn't review …"** on a pull request | The comment says why. Open that review in CodeLens and choose **Review again**, or push a new commit |
| **No review appears** | Check the App is installed on that repository (Settings → Install on more repositories), the pull request isn't a draft, and your account is in Settings → Accounts it works for |
| **A service keeps restarting** | The System page shows which, and its log. **Open the logs** on a status page opens the log folder |
| **"The database belongs to an earlier install"** | A database volume from an earlier install was left in Docker without its settings folder. To start fresh and lose that install's reviews: `docker volume rm codelens-desktop_pgdata` in a terminal |

The logs are in `%LOCALAPPDATA%\CodeLens\logs`: `desktop.log` (the app),
`compose.log` (starting the services) and `services\` (each service).

## 7. Removing CodeLens

**Settings → Apps → CodeLens → Uninstall.** It asks whether to keep your data:

- **Keep my data:** CodeLens's containers are removed; your reviews, settings and your
  App's key stay. Installing CodeLens again picks up where you left off.
- **Delete everything:** also the database, the data folder and CodeLens's images.

Either way, your **GitHub App stays on GitHub**. To remove it: GitHub → **Settings** →
**Developer settings** → **GitHub Apps** → your App → **Advanced** → **Delete GitHub App**.
Uninstalling with Delete everything offers to open that page.

Docker Desktop and Ollama stay installed; remove them from **Settings → Apps** if you
don't use them for anything else.

## Not on Windows?

CodeLens runs on macOS and Linux too, from source, with a terminal:
[`docs/run-your-own.md`](run-your-own.md).
