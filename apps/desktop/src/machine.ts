/** The first-run wizard's look at the machine (planning-v2.md §4, Phase 11
 * step 5): what CodeLens needs before anything is downloaded. Like
 * `codelens doctor`, each check is ok, a warning (it works, slower or with
 * less room) or a failure (it can't work), with what to do about it. The
 * decisions are plain functions over what the probes report, so they are
 * tested on any machine. */

import { spawn } from "node:child_process";
import { statfs } from "node:fs/promises";
import { arch, release, totalmem } from "node:os";

export type CheckStatus = "ok" | "warn" | "fail";

export type Check = { name: string; status: CheckStatus; detail: string; fix?: string };

const GiB = 1024 ** 3;

/** Docker Desktop's floor: Windows 10 22H2 (build 19045) or Windows 11, x64
 * (D148: x64 first). */
const MIN_BUILD = 19045;

export function checkWindows(osRelease: string, cpuArch: string): Check {
  const build = Number(osRelease.split(".")[2] ?? 0);
  if (cpuArch !== "x64") {
    return { name: "Windows", status: "fail", detail: `${cpuArch} Windows`, fix: "CodeLens Desktop needs 64-bit Intel or AMD (x64) Windows for now." };
  }
  if (build < MIN_BUILD) {
    return { name: "Windows", status: "fail", detail: `build ${build}`, fix: "Docker Desktop needs Windows 10 22H2 or Windows 11: update Windows first." };
  }
  return { name: "Windows", status: "ok", detail: build >= 22000 ? `Windows 11 (build ${build})` : `Windows 10 (build ${build})` };
}

/** Docker runs Linux in a small VM, so the processor's virtualization must be
 * on. With Hyper-V or WSL2 already running, Windows reports the firmware
 * setting as false and a hypervisor as present (seen on this machine), so
 * either one is enough. */
export function checkVirtualization(probe: { hypervisor: boolean; firmware: boolean } | null): Check {
  if (probe === null) {
    return { name: "Virtualization", status: "warn", detail: "couldn't be read", fix: "If Docker Desktop later says virtualization is off, turn it on in the BIOS/UEFI settings." };
  }
  if (probe.hypervisor || probe.firmware) {
    return { name: "Virtualization", status: "ok", detail: probe.hypervisor ? "on (a hypervisor is running)" : "on" };
  }
  return { name: "Virtualization", status: "fail", detail: "off", fix: "Turn on virtualization (Intel VT-x or AMD-V/SVM) in your BIOS/UEFI settings, then start CodeLens again." };
}

/** Docker's VM, the services and a model share it: 8 GB works, 16 GB is
 * comfortable (measured: ~0.5 GB for the stack, the VM's own share on top). */
export function checkMemory(bytes: number): Check {
  const gb = bytes / GiB;
  const detail = `${gb.toFixed(1)} GB`;
  if (gb < 7.5) return { name: "Memory", status: "fail", detail, fix: "CodeLens needs at least 8 GB of memory." };
  if (gb < 15) return { name: "Memory", status: "warn", detail, fix: "It works; close other large apps while it reviews." };
  return { name: "Memory", status: "ok", detail };
}

/** Docker Desktop (~4 GB), the images (~1.5 GB), the models (~6 GB) and the
 * repos' mirrors. */
export function checkDisk(freeBytes: number): Check {
  const gb = freeBytes / GiB;
  const detail = `${gb.toFixed(0)} GB free`;
  if (gb < 15) return { name: "Disk", status: "fail", detail, fix: "Free at least 15 GB: Docker Desktop, CodeLens and the models need about 12 GB." };
  if (gb < 30) return { name: "Disk", status: "warn", detail, fix: "It fits, with little room for large repositories." };
  return { name: "Disk", status: "ok", detail };
}

export type Gpu = { name: string; mib: number };

/** `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`. */
export function parseNvidiaSmi(output: string): Gpu[] {
  return output
    .split(/\r?\n/)
    .map((line) => line.split(","))
    .filter((parts) => parts.length >= 2 && Number.isFinite(Number(parts[1]?.trim())))
    .map((parts) => ({ name: (parts[0] ?? "").trim(), mib: Number(parts[1]?.trim()) }));
}

/** No GPU is a warning, not a failure: Ollama reviews on the CPU too, in a
 * few minutes instead of under one (D146, §8's risk). */
export function checkGpu(gpus: Gpu[]): Check {
  const best = [...gpus].sort((a, b) => b.mib - a.mib)[0];
  if (!best) {
    return { name: "GPU", status: "warn", detail: "no NVIDIA GPU found", fix: "Reviews run on the CPU: a few minutes each instead of under one." };
  }
  const detail = `${best.name}, ${(best.mib / 1024).toFixed(0)} GB`;
  if (best.mib < 7500) {
    return { name: "GPU", status: "warn", detail, fix: "qwen3:8b needs about 6 GB of video memory; part of it will run on the CPU, slower." };
  }
  return { name: "GPU", status: "ok", detail };
}

// --- the probes

/** A command's standard output, or null if it couldn't run or failed. */
export function output(command: string, args: string[]): Promise<string | null> {
  return new Promise((resolve) => {
    let text = "";
    const child = spawn(command, args, { windowsHide: true });
    child.stdout.on("data", (data: Buffer) => (text += data.toString()));
    child.on("error", () => resolve(null));
    child.on("close", (code) => resolve(code === 0 ? text : null));
  });
}

async function virtualization(): Promise<{ hypervisor: boolean; firmware: boolean } | null> {
  const script =
    "$c = Get-CimInstance Win32_ComputerSystem; $p = Get-CimInstance Win32_Processor | Select-Object -First 1; " +
    "[pscustomobject]@{ hypervisor = [bool]$c.HypervisorPresent; firmware = [bool]$p.VirtualizationFirmwareEnabled } | ConvertTo-Json -Compress";
  const text = await output("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script]);
  try {
    const value = JSON.parse(text ?? "") as { hypervisor: unknown; firmware: unknown };
    return { hypervisor: value.hypervisor === true, firmware: value.firmware === true };
  } catch {
    return null;
  }
}

/** Every check, for the folder the data will live in. */
export async function machineChecks(dataDrive: string): Promise<Check[]> {
  const [virt, smi, disk] = await Promise.all([
    virtualization(),
    output("nvidia-smi", ["--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]),
    statfs(dataDrive).catch(() => null),
  ]);
  return [
    checkWindows(release(), arch()),
    checkVirtualization(virt),
    checkMemory(totalmem()),
    disk ? checkDisk(disk.bavail * disk.bsize) : { name: "Disk", status: "warn", detail: "couldn't be read" },
    checkGpu(parseNvidiaSmi(smi ?? "")),
  ];
}
