import { ActivityIcon } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { Tag } from "@/components/badges";
import { TableSkeleton } from "@/components/skeleton";
import { StateMessage } from "@/components/state-message";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { isLocal } from "@/lib/config";
import { ControlError, getLogs, getSystem, LOG_SERVICES, type ServiceState, type SystemView } from "@/lib/control";
import { formatBytes } from "@/lib/format";
import { parseChoice } from "@/lib/params";
import { cn } from "@/lib/utils";

import { clearMirrors, openDataFolder, restartService } from "./actions";
import { ClearMirrorsButton, OpenFolderButton, RestartButton } from "./controls";

export const metadata: Metadata = { title: "System" };

/** CodeLens Desktop's System page (planning-v2.md Phase 11 step 6b, D176):
 * each service, Docker and Ollama, what the data folder holds, and each
 * service's recent log. Only in local mode, read by the app (D175). */
export default function SystemPage({ searchParams }: PageProps<"/system">) {
  return (
    <>
      <h1 className="text-xl font-semibold tracking-tight">System</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        What runs on this computer for CodeLens, and how it&apos;s doing. The app restarts anything that stops by
        itself; these are for a closer look.
      </p>
      <Suspense fallback={<TableSkeleton label="Loading the system" />}>
        <System searchParams={searchParams} />
      </Suspense>
    </>
  );
}

async function System({ searchParams }: Pick<PageProps<"/system">, "searchParams">) {
  await connection();
  if (!isLocal()) notFound();
  const logs = parseChoice((await searchParams).logs, LOG_SERVICES);
  let system: SystemView;
  try {
    system = await getSystem();
  } catch (error) {
    if (!(error instanceof ControlError)) throw error;
    return (
      <StateMessage className="mt-8" icon={<ActivityIcon />} title="The system can't be read">
        {`${error.message}. It comes from the CodeLens app itself: if it's restarting, try again in a moment.`}
      </StateMessage>
    );
  }
  const { storage } = system;
  return (
    <div className="mt-6 flex flex-col gap-6">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Services</CardTitle>
            <CardDescription>
              Docker {system.docker === "running" ? "is running" : `is ${system.docker}`}; Ollama{" "}
              {system.ollama ? "answers" : "doesn't answer (reviews fail until it does)"}
              {system.paused ? "; reviews are paused from the tray" : ""}.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ServicesTable services={system.services} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Storage</CardTitle>
            <CardDescription className="break-all">{storage.dataFolder}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-muted-foreground">Repositories&apos; mirrors</dt>
              <dd className="tabular-nums">{formatBytes(storage.mirrorsBytes)}</dd>
              <dt className="text-muted-foreground">Database</dt>
              <dd className="tabular-nums">{formatBytes(storage.databaseBytes)}</dd>
              <dt className="text-muted-foreground">Logs</dt>
              <dd className="tabular-nums">{formatBytes(storage.logsBytes)}</dd>
              <dt className="text-muted-foreground">Free on this drive</dt>
              <dd className="tabular-nums">{formatBytes(storage.freeBytes)}</dd>
            </dl>
            <div className="flex flex-wrap gap-2">
              <OpenFolderButton action={openDataFolder} />
              <ClearMirrorsButton action={clearMirrors} size={formatBytes(storage.mirrorsBytes)} />
            </div>
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Logs</CardTitle>
          <CardDescription>The last lines each service wrote, kept in the data folder&apos;s logs/services.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <nav aria-label="Which log" className="flex flex-wrap gap-1.5">
            {LOG_SERVICES.map((service) => (
              <Link
                key={service}
                href={`/system?logs=${service}`}
                scroll={false}
                aria-current={service === logs ? "page" : undefined}
                className={cn(
                  "rounded-md border border-border px-2.5 py-1 font-mono text-xs transition-colors hover:bg-muted",
                  service === logs && "border-primary bg-accent text-accent-foreground",
                )}
              >
                {service}
              </Link>
            ))}
          </nav>
          {logs ? (
            <Suspense key={logs} fallback={<TableSkeleton label={`Loading ${logs}'s log`} />}>
              <Log service={logs} />
            </Suspense>
          ) : (
            <p className="text-sm text-muted-foreground">Choose a service to see its log.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function ServicesTable({ services }: { services: ServiceState[] }) {
  if (services.length === 0) {
    return <p className="text-sm text-muted-foreground">No services are running: Docker isn&apos;t answering.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th scope="col" className="py-1.5 pr-3 font-medium">Service</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">State</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Health</th>
            <th scope="col" className="py-1.5 font-medium"><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {services.map((s) => (
            <tr key={s.service} className="border-t border-border">
              <td className="py-1.5 pr-3 font-mono text-xs">{s.service}</td>
              <td className="py-1.5 pr-3">
                <Tag>{s.state === "exited" ? `exited (${s.exitCode})` : s.state}</Tag>
              </td>
              <td className={cn("py-1.5 pr-3", s.health === "unhealthy" && "text-critical")}>{s.health || "—"}</td>
              <td className="py-1.5 text-right">
                <div className="flex justify-end gap-1">
                  <Link
                    href={`/system?logs=${s.service}`}
                    scroll={false}
                    className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    Log
                  </Link>
                  {s.service !== "migrate" && <RestartButton action={restartService} service={s.service} />}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

async function Log({ service }: { service: string }) {
  let lines: string[];
  try {
    ({ lines } = await getLogs(service));
  } catch (error) {
    if (!(error instanceof ControlError)) throw error;
    return <p className="text-sm text-critical">{error.message}</p>;
  }
  if (lines.length === 0) return <p className="text-sm text-muted-foreground">{service} hasn&apos;t written anything yet.</p>;
  return (
    <pre
      tabIndex={0}
      aria-label={`${service}'s last ${lines.length} lines`}
      className="max-h-[28rem] overflow-auto rounded-lg border border-border bg-muted/40 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-all"
    >
      {lines.join("\n")}
    </pre>
  );
}
