// The release's notes, for the release workflow (.github/workflows/release.yml):
// node scripts/release-notes.mjs <version> [release folder] > notes.md, after
// `pnpm dist`. Also writes SHA256SUMS.txt there, published next to the installer.
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";

import { installerName, releaseNotes } from "../dist/releasenotes.js";
import { readImages } from "../dist/stack.js";

const [version, dir = "release"] = process.argv.slice(2);
if (!version) throw new Error("usage: node scripts/release-notes.mjs <version> [release folder]");

const exe = installerName(version);
const sha256 = createHash("sha256").update(await readFile(`${dir}/${exe}`)).digest("hex");
await writeFile(`${dir}/SHA256SUMS.txt`, `${sha256}  ${exe}\n`);
process.stdout.write(releaseNotes(version, sha256, await readImages("images.json")));
