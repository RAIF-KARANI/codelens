import { describe, expect, it } from "vitest";

import { appAction, appOrigin, isAppUrl, isExternalLink, statusPage } from "../src/window.js";

describe("the window's guard", () => {
  it("keeps the dashboard of this launch in the window", () => {
    expect(appOrigin(49152)).toBe("http://127.0.0.1:49152");
    expect(isAppUrl("http://127.0.0.1:49152/reviews/3?x=1", 49152)).toBe(true);
  });

  it.each([
    "http://127.0.0.1:49153/",
    "http://localhost:49152/",
    "https://127.0.0.1:49152/",
    "http://127.0.0.1.evil.example:49152/",
    "https://github.com/",
    "file:///C:/Windows/win.ini",
    "not a url",
  ])("keeps %s out", (url) => {
    expect(isAppUrl(url, 49152)).toBe(false);
  });

  it("opens only https links outside", () => {
    expect(isExternalLink("https://github.com/RAIF-KARANI/codelens")).toBe(true);
    for (const url of ["http://example.com", "file:///C:/x", "javascript:alert(1)", "ms-settings:", "x"]) {
      expect(isExternalLink(url), url).toBe(false);
    }
  });
});

describe("statusPage", () => {
  it("is a data: page with the text escaped", () => {
    const url = statusPage("Starting <CodeLens>", 'a "b" & c');
    expect(url.startsWith("data:text/html;charset=utf-8,")).toBe(true);
    const html = decodeURIComponent(url.slice(url.indexOf(",") + 1));
    expect(html).toContain("Starting &lt;CodeLens&gt;");
    expect(html).toContain("a &quot;b&quot; &amp; c");
    expect(html).not.toContain("<CodeLens>");
  });
});

describe("the status pages' buttons", () => {
  it("are codelens:// actions the window carries out, and nothing else", () => {
    expect(appAction("codelens://retry")).toBe("retry");
    expect(appAction("codelens://start-docker/")).toBe("start-docker");
    expect(appAction("codelens://open-logs")).toBe("open-logs");
    for (const url of ["codelens://rm-rf", "codelens://retry?x=1", "https://codelens/retry", "retry"]) {
      expect(appAction(url), url).toBeNull();
    }
  });

  it("render as links, the first one primary, with no script", () => {
    const url = statusPage("Docker Desktop isn't running", "Start it.", {
      buttons: [
        { label: "Start Docker Desktop", href: "codelens://start-docker" },
        { label: "Try again", href: "codelens://retry" },
      ],
      busy: false,
    });
    const html = decodeURIComponent(url.slice(url.indexOf(",") + 1));
    expect(html).toContain('<a class="primary" href="codelens://start-docker">Start Docker Desktop</a>');
    expect(html).toContain('<a class="" href="codelens://retry">Try again</a>');
    expect(html).not.toContain("<script");
    expect(html).not.toContain('class="dot"');
  });
});
