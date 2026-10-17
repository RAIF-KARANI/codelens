import { describe, expect, it } from "vitest";

import { appOrigin, isAppUrl, isExternalLink, statusPage } from "../src/window.js";

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
