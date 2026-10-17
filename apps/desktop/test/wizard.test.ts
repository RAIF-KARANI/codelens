import { describe, expect, it } from "vitest";

import { formatBytes, progressLabel, progressScript, wizardPage } from "../src/wizard.js";

const html = (url: string) => decodeURIComponent(url.slice(url.indexOf(",") + 1));

describe("wizardPage", () => {
  it("lists the steps with where it is, for sight and for screen readers", () => {
    const page = html(
      wizardPage({
        marks: { machine: "done", docker: "active" },
        title: "Install Docker Desktop",
        detail: "From docker.com.",
        buttons: [{ label: "Download and install", href: "codelens://install-docker" }],
      }),
    );
    expect(page).toContain('<li class="done">Check this computer<span class="sr">: done</span></li>');
    expect(page).toContain('<li class="active" aria-current="step">Docker Desktop');
    expect(page).toContain('<li class="todo">Ollama</li>');
    expect(page).toContain('<a class="primary" href="codelens://install-docker">Download and install</a>');
    expect(page).not.toContain("<script");
  });

  it("shows the checks with their fixes, escaped", () => {
    const page = html(
      wizardPage({
        marks: { machine: "failed" },
        title: "t",
        detail: "d",
        checks: [{ name: "Disk", status: "fail", detail: "<5 GB>", fix: "Free some space & retry" }],
      }),
    );
    expect(page).toContain('<li class="fail"><span class="icon" aria-hidden="true">✕</span>');
    expect(page).toContain("&lt;5 GB&gt;");
    expect(page).toContain("Free some space &amp; retry");
  });

  it("draws a progress bar, determinate or not", () => {
    const known = html(wizardPage({ marks: {}, title: "t", detail: "d", progress: { label: "1 of 2", fraction: 0.5 } }));
    expect(known).toContain('aria-valuenow="50"');
    expect(known).toContain('style="width:50%"');
    const unknown = html(wizardPage({ marks: {}, title: "t", detail: "d", progress: { label: "…", fraction: null } }));
    expect(unknown).toContain('class="bar busy"');
    expect(unknown).not.toContain("aria-valuenow");
  });
});

describe("progress", () => {
  it("labels sizes as people read them", () => {
    expect(formatBytes(636_811_696)).toBe("607 MB");
    expect(formatBytes(1_584_629_376)).toBe("1.48 GB");
    expect(progressLabel(1_288_490_189, 1_584_629_376)).toBe("1.20 GB of 1.48 GB");
    expect(progressLabel(10 * 1024 ** 2, null)).toBe("10 MB");
  });

  it("moves the bar with a script whose values can't become code", () => {
    const script = progressScript(0.42, '"); alert(1); ("');
    expect(script).toContain("const width = 42;");
    expect(script).toContain(JSON.stringify('"); alert(1); ("'));
    expect(() => new Function(script)).not.toThrow();
    expect(progressScript(2, "x")).toContain("const width = 100;");
    expect(progressScript(null, "x")).toContain("const width = null;");
  });
});
