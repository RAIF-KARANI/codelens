# What's new in CodeLens Desktop

Each release's notes, for the people who use it. The release workflow puts a version's
section at the top of its notes on GitHub, and the download page shows the latest one; a
tag whose version has no section here isn't released. One line per change, saying what
it means for you.

## 0.1.1 (2026-10-19)

- Reviews no longer fail when Ollama starts after CodeLens, as it often does when Windows
  starts: a review waits for it, up to 10 minutes, and says so in its timeline.
- A review that failed because Ollama wasn't running is reviewed again by itself once
  Ollama answers.
- A failed review's page has **Review again**.
- Reviews that were waiting when Windows restarted are no longer lost: CodeLens picks
  them up when it starts again.
- Uninstalling no longer leaves an empty CodeLens folder behind.
- A user's guide, from the download to troubleshooting: `docs/desktop.md`.

## 0.1.0 (2026-10-18)

- The first release: a Windows installer, a setup that installs Docker Desktop and Ollama
  if they're missing, downloads the models and creates your own GitHub App, then reviews
  your pull requests on your own PC.
- The dashboard in its window, with Settings and System pages, and the tray: pause,
  start with Windows, quit.
- Updates from GitHub, installed only when you say so, with a database backup before
  any update that changes it.
- An uninstaller that asks whether to keep your data.
