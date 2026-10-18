/** Each release's notes on GitHub (planning-v2.md Phase 11 step 7): the
 * download, what to do about SmartScreen's warning (the installer is
 * unsigned, D148), how to check the file, what the computer needs, and the
 * images this version runs. The release workflow writes them
 * (scripts/release-notes.mjs); the README's download section says the same,
 * shorter. The app uses REPO and the release page's address (updates.ts). */

import type { Images } from "./stack.js";

export const REPO = "RAIF-KARANI/codelens";

export function installerName(version: string): string {
  return `CodeLens-Setup-${version}.exe`;
}

export function releaseNotes(version: string, sha256: string, images: Images): string {
  const exe = installerName(version);
  const url = `https://github.com/${REPO}/releases/download/v${version}/${exe}`;
  return `## Download

**[${exe}](${url})**: Windows 10 (22H2) or 11, 64-bit.

Run it, then follow the setup in the window. It checks this computer; installs Docker Desktop and Ollama if they're missing (from their publishers, after checking their signatures); downloads the AI models (about 6 GB); and creates your own GitHub App, to install on the repositories you want reviewed. Nothing of anyone else's is involved: the reviews run on this computer.

### "Windows protected your PC"

CodeLens isn't code-signed, so Windows SmartScreen doesn't recognise it yet. Choose **More info**, then **Run anyway**.

To check the file is the one this release built, compare its SHA-256 with the one below. In PowerShell:

\`\`\`powershell
Get-FileHash .\\${exe}
\`\`\`

\`\`\`
${sha256.toUpperCase()}
\`\`\`

### What the computer needs

- Virtualization on (Docker Desktop runs a small Linux VM).
- 8 GB of memory at least, 16 GB is comfortable.
- 15 GB free on the disk at least, 30 GB is comfortable.
- An NVIDIA GPU with 6 GB or more is best: a review then takes under a minute. Without one, reviews run on the CPU, in a few minutes each.

### Uninstalling

**Settings → Apps → CodeLens → Uninstall.** It asks whether to keep your data (your reviews, settings and your GitHub App's key). Kept, installing CodeLens again picks up where you left off.

### The images this version runs

Pinned by digest; each is what CI built and published from this repository.

| Service | Image |
|---|---|
| api | \`${images.api}\` |
| worker | \`${images.worker}\` |
| web | \`${images.web}\` |
`;
}
