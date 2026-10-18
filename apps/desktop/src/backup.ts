/** The database backup before an update's migrations (planning-v2.md Phase 11
 * step 7b, D151). Migrations are never edited once applied (D29), so a bad
 * one can only be undone by going back to a copy taken before it ran.
 *
 * On the first start of a new version, before the stack's `migrate` runs:
 * Postgres starts alone; the new API image's Alembic says where the database
 * is (`alembic current`, which marks the revision "(head)" when this image has
 * nothing newer); if it isn't at this image's head, `pg_dump` writes the whole
 * database to backups/ in the data folder, the last three kept. A backup that
 * fails stops the start: the migrations don't run without one.
 *
 * Plain functions for the decisions, so they're tested without Docker. */

import { spawn } from "node:child_process";
import { createWriteStream } from "node:fs";
import { mkdir, readdir, rm, stat } from "node:fs/promises";
import { join } from "node:path";

import { composeArgs, composeEnv, docker, dockerBin, type Images, type Launch, type Run, type Stack } from "./stack.js";

/** How many backups stay (the user's choice, 2026-10-18). */
export const KEEP_BACKUPS = 3;

export type Revision = { revision: string; atHead: boolean } | null;

/** `alembic current` as it prints, logging lines and all: the revision, and
 * whether it is this image's head. Null: an empty database, nothing to keep.
 * The whole line must be the revision: a first start after an update pulls
 * the image, and Docker's pull lines start with hex layer ids too ("4f4fb700ef54
 * Pull complete", found live). */
export function parseCurrent(output: string): Revision {
  for (const line of output.split(/\r?\n/)) {
    const match = /^([0-9a-f]{12})( \(head\))?$/.exec(line.trim());
    if (match?.[1]) return { revision: match[1], atHead: Boolean(match[2]) };
  }
  return null;
}

/** codelens-<the version backed up from>-<when>.dump; sortable by time. */
export function backupName(fromVersion: string, at: Date): string {
  const stamp = at.toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
  return `codelens-${fromVersion.replace(/[^\w.-]/g, "_")}-${stamp}.dump`;
}

/** The backups to delete: all but the newest `keep`, by the time in the name. */
export function oldBackups(names: string[], keep = KEEP_BACKUPS): string[] {
  const dumps = names.filter((n) => /^codelens-.+-\d{8}T\d{6}Z\.dump$/.test(n));
  const time = (n: string) => n.slice(-"20261018T120000Z.dump".length);
  dumps.sort((a, b) => time(b).localeCompare(time(a)));
  return dumps.slice(keep);
}

/** CodeLens's published images on this machine that the running version
 * doesn't pin: from `docker image ls --no-trunc --format "{{.ID}}
 * {{.Repository}}"`, and the pinned ones' ids (`docker image inspect`). By id,
 * since an older image left untagged by an update shows no digest. They
 * belong to versions before an update, ~100-200 MB each. */
export function staleImages(lines: string, pinnedIds: string[]): string[] {
  const pinned = new Set(pinnedIds.map((id) => id.trim()).filter(Boolean));
  const stale = new Set<string>();
  for (const line of lines.split(/\r?\n/)) {
    const [id, repository] = line.trim().split(/\s+/);
    if (!id || !/^ghcr\.io\/raif-karani\/codelens-(api|worker|web)$/.test(repository ?? "")) continue;
    if (!pinned.has(id)) stale.add(id);
  }
  return [...stale];
}

/** Removes the images `staleImages` finds; one still in use stays (Docker
 * refuses), which is fine. Never run unless the pinned ones are all here. */
export async function removeStaleImages(images: Images): Promise<string[]> {
  const pinned = await docker(["image", "inspect", "--format", "{{.Id}}", ...Object.values(images)]);
  if (pinned.code !== 0) return [];
  const listed = await docker(["image", "ls", "--no-trunc", "--format", "{{.ID}} {{.Repository}}"]);
  const stale = staleImages(listed.output, pinned.output.split(/\r?\n/));
  if (stale.length > 0) await docker(["image", "rm", ...stale]);
  return stale;
}

export type BackupResult =
  | { kind: "not-needed"; revision: Revision }
  | { kind: "done"; file: string; bytes: number }
  | { kind: "failed"; why: string };

/** Postgres alone, then where its database is, then the backup if the new
 * image would migrate it. */
export async function backupBeforeMigrating(
  stack: Stack,
  launch: Launch,
  fromVersion: string,
  now: Date,
  onOutput?: (chunk: string) => void,
): Promise<BackupResult> {
  const env = composeEnv(stack, launch);
  const pg = await docker(composeArgs(stack, "up", "-d", "--wait", "--pull", "missing", "postgres"), env, onOutput);
  if (pg.code !== 0) return { kind: "failed", why: "the database didn't start" };
  const current = await docker(
    composeArgs(stack, "run", "--rm", "--no-deps", "-T", "migrate", "alembic", "-c", "packages/core/alembic.ini", "current"),
    env,
    onOutput,
  );
  if (current.code !== 0) return { kind: "failed", why: "the new version couldn't read the database's revision" };
  const revision = parseCurrent(current.output);
  if (!revision || revision.atHead) return { kind: "not-needed", revision };

  const dir = join(stack.home, "backups");
  await mkdir(dir, { recursive: true });
  const file = join(dir, backupName(fromVersion, now));
  const run = await dumpTo(composeArgs(stack, "exec", "-T", "postgres", "pg_dump", "-U", "codelens", "-d", "codelens", "-Fc"), env, file);
  const bytes = (await stat(file).catch(() => null))?.size ?? 0;
  if (run.code !== 0 || bytes === 0) {
    await rm(file, { force: true });
    return { kind: "failed", why: `pg_dump failed: ${run.output.trim().split(/\r?\n/).at(-1) ?? `exit ${run.code}`}` };
  }
  for (const old of oldBackups(await readdir(dir))) await rm(join(dir, old), { force: true });
  return { kind: "done", file, bytes };
}

/** `docker …` with its standard output written to a file (a dump is binary,
 * and can be large); standard error kept to say why it failed. */
function dumpTo(args: string[], env: Record<string, string>, file: string): Promise<Run> {
  return new Promise((resolve, reject) => {
    const out = createWriteStream(file, { mode: 0o600 });
    const child = spawn(dockerBin(), args, { env: { ...process.env, ...env }, windowsHide: true });
    let errors = "";
    child.stdout.pipe(out);
    child.stderr.on("data", (data: Buffer) => (errors += data.toString()));
    child.on("error", reject);
    child.on("close", (code) => out.end(() => resolve({ code: code ?? 1, output: errors })));
  });
}
