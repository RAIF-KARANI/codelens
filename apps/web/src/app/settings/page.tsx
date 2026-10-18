import { SettingsIcon } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { Tag } from "@/components/badges";
import { TableSkeleton } from "@/components/skeleton";
import { StateMessage } from "@/components/state-message";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { isLocal } from "@/lib/config";
import { ControlError, getSettings, releasePage, type SettingsView } from "@/lib/control";

import { saveApp, saveEvents, saveUpdates, setWindow, updateNow } from "./actions";
import { AppForm, EventsForm, UpdatesForm, WindowForm } from "./forms";

export const metadata: Metadata = { title: "Settings" };

/** CodeLens Desktop's settings (planning-v2.md Phase 11 step 6a, D175): only
 * in local mode, where they belong to the machine's one owner; read and
 * applied by the app, through its control API. */
export default function SettingsPage() {
  return (
    <>
      <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        How CodeLens works on this computer. Saving restarts the services that use a setting.
      </p>
      <Suspense fallback={<TableSkeleton label="Loading settings" />}>
        <Settings />
      </Suspense>
    </>
  );
}

async function Settings() {
  // Whether this is the desktop is decided where it runs, not when it's built.
  await connection();
  if (!isLocal()) notFound();
  let settings: SettingsView;
  try {
    settings = await getSettings();
  } catch (error) {
    if (!(error instanceof ControlError)) throw error;
    return (
      <StateMessage className="mt-8" icon={<SettingsIcon />} title="The settings can't be read">
        {`${error.message}. They come from the CodeLens app itself: if it's restarting, try again in a moment.`}
      </StateMessage>
    );
  }
  const { app, events, models, updates } = settings;
  return (
    <div className="mt-6 grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Your GitHub App</CardTitle>
          <CardDescription>
            {app.configured ? `App id ${app.appId}. ` : "Not created yet. "}
            Who it reviews for, and how much.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-2">
            {app.installUrl && (
              <Button asChild variant="outline" size="sm">
                <a href={app.installUrl}>Install on more repositories</a>
              </Button>
            )}
            {app.manageUrl && (
              <Button asChild variant="outline" size="sm">
                <a href={app.manageUrl}>The App on GitHub</a>
              </Button>
            )}
          </div>
          <AppForm action={saveApp} accounts={app.allowedAccounts} reviewsPerHour={app.reviewsPerHour} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Pull requests</CardTitle>
          <CardDescription>How CodeLens hears about pushes and pull requests.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <EventsForm action={saveEvents} pollIntervalS={events.pollIntervalS} />
          <p className="text-sm text-muted-foreground">
            Webhooks through a relay aren&apos;t offered here yet: the App was made with its webhook off,
            and only GitHub&apos;s settings page can switch it on.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Models</CardTitle>
          <CardDescription>Run by Ollama on this computer.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <ul className="flex flex-col gap-2 text-sm">
            {[
              [models.llm, "reviews"],
              [models.embed, "finds related code"],
            ].map(([name, role]) => (
              <li key={name} className="flex flex-wrap items-center gap-2">
                <code className="font-mono">{name}</code>
                <span className="text-muted-foreground">{role}</span>
                <Tag>{models.pulled[name] ? "pulled" : "not pulled"}</Tag>
              </li>
            ))}
          </ul>
          <WindowForm action={setWindow} contextWindow={models.contextWindow} ollamaReady={models.ollamaReadyForLong} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Repositories</CardTitle>
          <CardDescription>Which ones the App may read is chosen on GitHub.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href="/repos">Repositories here</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <a href="https://github.com/settings/installations">Choose on GitHub</a>
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Updates</CardTitle>
          <CardDescription>
            CodeLens {updates.current} on this computer. New releases come from GitHub, and install only when you say so.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <UpdatesForm
            update={updateNow}
            save={saveUpdates}
            updates={updates}
            releaseUrl={updates.available ? releasePage(updates.available) : null}
          />
        </CardContent>
      </Card>
    </div>
  );
}
