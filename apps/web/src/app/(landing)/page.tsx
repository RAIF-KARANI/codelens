import { DownloadIcon, LayoutDashboardIcon, TerminalIcon } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { isDemo } from "@/lib/config";
import { formatBytes } from "@/lib/format";
import { ALL_RELEASES_URL, latestRelease, RELEASES_URL } from "@/lib/release";

export const metadata: Metadata = {
  title: { absolute: "CodeLens: AI code reviews on your own PC" },
  description:
    "An AI code reviewer for GitHub pull requests that runs on your own GPU. Download CodeLens Desktop for Windows, see the live demo, or self-host it.",
};

const SELF_HOST_URL = "https://github.com/RAIF-KARANI/codelens/blob/main/docs/run-your-own.md";

const STEPS = [
  ["Run the installer", "Windows says “Windows protected your PC”, since CodeLens isn't code-signed yet: choose More info, then Run anyway."],
  ["Follow the setup", "It checks your PC, installs Docker Desktop and Ollama if they're missing, and downloads the AI models (about 6 GB)."],
  ["Create your GitHub App", "One click on GitHub makes an App that is yours alone; install it on the repositories you want reviewed."],
  ["Open a pull request", "Each push is reviewed, usually within a minute on an NVIDIA GPU, with the findings as comments on the pull request."],
] as const;

/** The demo site's front page (D180): what CodeLens is, the launch video, and
 * three ways in: the Windows app, the live demo, self-hosting. Only on the
 * demo: anywhere else "/" is the dashboard (src/proxy.ts forwards it). The
 * release comes from GitHub, cached for an hour (lib/release.ts); if GitHub
 * doesn't answer, the button goes to the Releases page. */
export default async function LandingPage() {
  if (!isDemo()) redirect("/overview");
  const release = await latestRelease();
  return (
    <div className="flex flex-col gap-14">
      <section aria-labelledby="landing-title" className="grid items-center gap-10 lg:grid-cols-[1fr_1.15fr]">
        <div className="flex flex-col items-start gap-5">
          <h1 id="landing-title" className="text-3xl font-semibold tracking-tight text-balance md:text-4xl">
            AI code reviews of your pull requests, from a model on your own PC
          </h1>
          <p className="text-muted-foreground text-pretty">
            CodeLens turns a repository into a code graph, reviews each pull request with a local model and the code
            around the change, and posts its findings as comments, usually within a minute. Every finding comes with
            the evidence it was based on. Your code goes between GitHub and your machine, and nowhere else.
          </p>
          <div className="flex w-full flex-col gap-2.5 sm:w-auto">
            <Button asChild size="lg" className="h-11 justify-start px-4 text-base">
              <a href={release?.url ?? RELEASES_URL}>
                <DownloadIcon className="size-5" aria-hidden="true" />
                {release ? `Download CodeLens ${release.version} for Windows` : "Download CodeLens for Windows"}
              </a>
            </Button>
            <Button asChild variant="outline" size="lg" className="h-11 justify-start px-4 text-base">
              <Link href="/overview">
                <LayoutDashboardIcon className="size-5" aria-hidden="true" />
                See the live demo
              </Link>
            </Button>
            <Button asChild variant="outline" size="lg" className="h-11 justify-start px-4 text-base">
              <a href={SELF_HOST_URL}>
                <TerminalIcon className="size-5" aria-hidden="true" />
                Self-host (any OS)
              </a>
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">
            {release ? `${formatBytes(release.bytes)} · Windows 10 or 11, 64-bit · ` : "Windows 10 or 11, 64-bit · "}
            <a href={release?.pageUrl ?? RELEASES_URL} className="underline underline-offset-4 hover:text-foreground">
              Release notes
            </a>
            . Self-hosting needs Docker, Ollama and a terminal, on Windows, macOS or Linux.
          </p>
        </div>
        <figure className="flex flex-col gap-2">
          <video
            controls
            playsInline
            preload="metadata"
            poster="/media/codelens-launch.jpg"
            className="aspect-video w-full rounded-xl border border-border bg-black shadow-sm"
          >
            <source src="/media/codelens-launch.mp4" type="video/mp4" />
            <track kind="captions" src="/media/codelens-launch.vtt" srcLang="en" label="English" />
          </video>
          <figcaption className="text-sm text-muted-foreground">
            24 seconds: one changed line, reviewed by a local model, caught, and traced back to the code it cites.
          </figcaption>
        </figure>
      </section>

      {release && release.whatsNew.length > 0 && (
        <section aria-labelledby="whats-new-title" className="flex flex-col gap-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="whats-new-title" className="text-lg font-semibold tracking-tight">
              What&apos;s new in {release.version}
            </h2>
            <a href={ALL_RELEASES_URL} className="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground">
              Every release
            </a>
          </div>
          <Card>
            <CardContent>
              <ul className="flex list-disc flex-col gap-1.5 pl-5 text-sm">
                {release.whatsNew.map((item) => (
                  <li key={item}>
                    <Inline text={item} />
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-sm text-muted-foreground">
                Already installed? CodeLens offers each new release itself: Settings → Updates, or the tray.
              </p>
            </CardContent>
          </Card>
        </section>
      )}

      <section aria-labelledby="steps-title" className="flex flex-col gap-4">
        <h2 id="steps-title" className="text-lg font-semibold tracking-tight">
          After the download
        </h2>
        <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map(([title, body], i) => (
            <li key={title}>
              <Card className="h-full">
                <CardHeader>
                  <span className="text-sm font-medium text-primary tabular-nums" aria-hidden="true">
                    {i + 1}
                  </span>
                  <CardTitle>{title}</CardTitle>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground">{body}</CardContent>
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
              <li>Windows 10 (22H2) or 11, 64-bit, with virtualization on</li>
              <li>8 GB of memory at least; 16 GB is comfortable</li>
              <li>15 GB free on the disk at least; 30 GB is comfortable</li>
              <li>An NVIDIA GPU with 6 GB or more is best; without one, reviews take a few minutes each</li>
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
                <p>
                  In PowerShell, <code className="font-mono">Get-FileHash .\CodeLens-Setup-{release.version}.exe</code>{" "}
                  should print:
                </p>
                <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs break-all whitespace-pre-wrap">
                  {release.sha256}
                </pre>
              </>
            ) : (
              <p>Each release&apos;s notes give the installer&apos;s SHA-256, and how to check it.</p>
            )}
            <p className="text-muted-foreground">
              To remove it later: Settings → Apps → CodeLens → Uninstall. It asks whether to keep your data.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

/** The release notes' **bold** and `code`, as elements: nothing from the
 * notes is ever HTML. */
function Inline({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\*\*[^*]+\*\*|`[^`]+`)/).map((part, i) =>
        part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
          <b key={i}>{part.slice(2, -2)}</b>
        ) : part.startsWith("`") && part.endsWith("`") && part.length > 2 ? (
          <code key={i} className="font-mono text-[0.85em]">
            {part.slice(1, -1)}
          </code>
        ) : (
          part
        ),
      )}
    </>
  );
}
