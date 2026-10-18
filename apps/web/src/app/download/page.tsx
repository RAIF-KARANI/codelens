import { CpuIcon, DownloadIcon, FolderGit2Icon, GitPullRequestIcon, ShieldCheckIcon } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatBytes } from "@/lib/format";
import { latestRelease, RELEASES_URL } from "@/lib/release";

export const metadata: Metadata = {
  title: "Download",
  description: "CodeLens Desktop for Windows: AI code reviews of your pull requests, run on your own PC.",
};

const STEPS = [
  {
    icon: <ShieldCheckIcon />,
    title: "Run the installer",
    body: "CodeLens isn't code-signed yet, so Windows says “Windows protected your PC”. Choose More info, then Run anyway.",
  },
  {
    icon: <CpuIcon />,
    title: "Follow the setup",
    body: "It checks your PC, installs Docker Desktop and Ollama if they're missing (from their makers, signatures checked), and downloads the AI models, about 6 GB.",
  },
  {
    icon: <FolderGit2Icon />,
    title: "Create your GitHub App",
    body: "One click on GitHub makes an App that is yours alone. Install it on the repositories you want reviewed.",
  },
  {
    icon: <GitPullRequestIcon />,
    title: "Open a pull request",
    body: "Each push is reviewed, usually within a minute on an NVIDIA GPU. The findings appear as comments on the pull request, and in the app with their evidence.",
  },
];

const NEEDS = [
  "Windows 10 (22H2) or Windows 11, 64-bit",
  "Virtualization on (Docker Desktop runs a small Linux VM)",
  "8 GB of memory at least; 16 GB is comfortable",
  "15 GB free on the disk at least; 30 GB is comfortable",
  "An NVIDIA GPU with 6 GB or more is best; without one, reviews take a few minutes each",
];

/** CodeLens Desktop's download page (Phase 11, D179): what it is, the
 * download, and what happens next. The release comes from GitHub, cached for
 * an hour (lib/release.ts); if GitHub doesn't answer, the button goes to the
 * Releases page instead. */
export default async function DownloadPage() {
  const release = await latestRelease();
  return (
    <div className="flex flex-col gap-10">
      <section aria-labelledby="download-title" className="flex flex-col items-start gap-4">
        <p className="text-sm font-medium text-primary">CodeLens Desktop for Windows</p>
        <h1 id="download-title" className="max-w-3xl text-3xl font-semibold tracking-tight text-balance md:text-4xl">
          AI code reviews of your pull requests, from a model on your own PC
        </h1>
        <p className="max-w-2xl text-muted-foreground text-pretty">
          CodeLens reads each pull request on your GitHub repositories with the code around the change, and posts
          what it finds as review comments. The model runs on your computer: your code goes between GitHub and your
          PC, and nowhere else. Free and open source.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Button asChild size="lg" className="h-11 px-5 text-base">
            <a href={release?.url ?? RELEASES_URL}>
              <DownloadIcon className="size-5" aria-hidden="true" />
              {release ? `Download CodeLens ${release.version}` : "Download from GitHub"}
            </a>
          </Button>
          <Button asChild variant="outline" size="lg" className="h-11 px-4">
            <Link href="/">See the demo first</Link>
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">
          {release
            ? `${formatBytes(release.bytes)} · Windows 10 or 11, 64-bit · `
            : "Windows 10 or 11, 64-bit · "}
          <a href={release?.pageUrl ?? RELEASES_URL} className="underline underline-offset-4 hover:text-foreground">
            Release notes
          </a>
          {" · "}
          <a href="https://github.com/RAIF-KARANI/codelens" className="underline underline-offset-4 hover:text-foreground">
            Source on GitHub
          </a>
        </p>
      </section>

      <section aria-labelledby="steps-title" className="flex flex-col gap-4">
        <h2 id="steps-title" className="text-lg font-semibold tracking-tight">
          What happens after the download
        </h2>
        <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((step, i) => (
            <li key={step.title}>
              <Card className="h-full">
                <CardHeader>
                  <div className="flex items-center gap-2 text-primary [&_svg]:size-5" aria-hidden="true">
                    {step.icon}
                    <span className="text-sm font-medium tabular-nums">{i + 1}</span>
                  </div>
                  <CardTitle>{step.title}</CardTitle>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground">{step.body}</CardContent>
              </Card>
            </li>
          ))}
        </ol>
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>What your PC needs</CardTitle>
            <CardDescription>The setup checks all of this before it downloads anything.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="flex list-disc flex-col gap-1.5 pl-5 text-sm">
              {NEEDS.map((need) => (
                <li key={need}>{need}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Check the download</CardTitle>
            <CardDescription>The file should be exactly the one GitHub built for this release.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm">
            {release?.sha256 ? (
              <>
                <p>In PowerShell, in your Downloads folder:</p>
                <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs">
                  Get-FileHash .\CodeLens-Setup-{release.version}.exe
                </pre>
                <p>It should print this SHA-256:</p>
                <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs break-all whitespace-pre-wrap">
                  {release.sha256}
                </pre>
              </>
            ) : (
              <p>
                Each release&apos;s notes give the installer&apos;s SHA-256, and how to check it with{" "}
                <code className="font-mono">Get-FileHash</code>.
              </p>
            )}
            <p className="text-muted-foreground">
              To remove CodeLens later: Settings → Apps → CodeLens → Uninstall. It asks whether to keep your data.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
