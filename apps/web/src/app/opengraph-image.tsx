import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { ImageResponse } from "next/og";

/** The picture a shared link shows (Open Graph; X falls back to it): what
 * CodeLens is, beside the demo's real finding, a SQL injection with its
 * evidence (the launch video's scene). At the app's root, so every page has
 * it; rendered once, when the app is built. The dark theme's colours
 * (globals.css), in Geist, the site's font. */

export const alt =
  "CodeLens: AI code reviews of your pull requests, from a model on your own PC. A pull request's diff with a critical finding, a potential SQL injection, and the code it cites as evidence.";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const C = {
  background: "#0e0c14",
  card: "#17141f",
  border: "#2a2536",
  foreground: "#ecebf3",
  muted: "#9a95ad",
  primary: "#9d8cf5",
  accent: "#231d3d",
  critical: "#f87171",
  criticalSoft: "#2a1215",
  added: "#34d399",
  addedSoft: "rgba(16, 185, 129, 0.16)",
};

// The fonts are the app's own files (assets/og, with Geist's licence), not
// read from node_modules/geist: on Vercel that's a pnpm symlink the function
// doesn't get, and since every page's metadata loads this module, the missing
// file broke every page (D185).
const font = (file: string) => readFile(join(process.cwd(), "assets", "og", file));

// Read once, when the module loads: inside the handler they would make it
// request-time work, rendered on every share instead of once at build.
const [regular, semibold, mono] = await Promise.all([
  font("Geist-Regular.ttf"),
  font("Geist-SemiBold.ttf"),
  font("GeistMono-Regular.ttf"),
]);

function Mark({ size: px }: { size: number }) {
  return (
    <svg width={px} height={px} viewBox="0 0 32 32">
      <circle cx="16" cy="16" r="13" fill="none" stroke={C.primary} strokeWidth="3" />
      <path d="M11 19 L16 11 L21 19 Z" fill="none" stroke={C.primary} strokeWidth="1.6" strokeLinejoin="round" />
      <circle cx="16" cy="11" r="2.6" fill={C.primary} />
      <circle cx="11" cy="19" r="2.6" fill={C.primary} />
      <circle cx="21" cy="19" r="2.6" fill={C.primary} />
    </svg>
  );
}

function Chip({ children }: { children: string }) {
  return (
    <div
      style={{
        display: "flex",
        padding: "6px 14px",
        borderRadius: 999,
        border: `1px solid ${C.border}`,
        background: C.accent,
        color: "#cfc6fb",
        fontSize: 20,
      }}
    >
      {children}
    </div>
  );
}

function CodeLine({ n, sign, text, added }: { n: string; sign: string; text: string; added?: boolean }) {
  return (
    <div
      style={{
        display: "flex",
        gap: 14,
        padding: "5px 18px",
        background: added ? C.addedSoft : "transparent",
        color: added ? C.foreground : C.muted,
        fontFamily: "Geist Mono",
        fontSize: 17,
      }}
    >
      <span style={{ width: 40, color: C.muted, opacity: 0.7 }}>{n}</span>
      <span style={{ width: 12, color: added ? C.added : C.muted }}>{sign}</span>
      <span>{text}</span>
    </div>
  );
}

export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          padding: "64px 64px 56px",
          gap: 48,
          background: `radial-gradient(circle at 85% 20%, rgba(157, 140, 245, 0.22), transparent 55%), ${C.background}`,
          color: C.foreground,
          fontFamily: "Geist",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", width: 560 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <Mark size={44} />
            <span style={{ fontSize: 32, fontWeight: 600, letterSpacing: -0.5 }}>CodeLens</span>
          </div>
          <div
            style={{
              display: "flex",
              marginTop: 44,
              fontSize: 50,
              fontWeight: 600,
              lineHeight: 1.08,
              letterSpacing: -1.5,
            }}
          >
            AI code reviews of your pull requests, from a model on your own PC
          </div>
          <div style={{ display: "flex", marginTop: 24, fontSize: 24, lineHeight: 1.4, color: C.muted }}>
            It reads the code around each change, and every finding shows the evidence it was based on.
          </div>
          <div style={{ display: "flex", gap: 10, marginTop: "auto" }}>
            <Chip>Windows app</Chip>
            <Chip>Self-host</Chip>
            <Chip>Open source</Chip>
          </div>
          <div style={{ display: "flex", marginTop: 18, fontFamily: "Geist Mono", fontSize: 20, color: C.muted }}>
            codelens.raifkarani.in
          </div>
        </div>

        <div style={{ display: "flex", flex: 1, alignItems: "center" }}>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              width: "100%",
              borderRadius: 18,
              border: `1px solid ${C.border}`,
              background: C.card,
              overflow: "hidden",
              boxShadow: "0 24px 60px rgba(0, 0, 0, 0.45)",
            }}
          >
            <div
              style={{
                display: "flex",
                padding: "14px 18px",
                borderBottom: `1px solid ${C.border}`,
                fontFamily: "Geist Mono",
                fontSize: 17,
                color: C.foreground,
              }}
            >
              sqlite_utils/db.py
            </div>
            <div style={{ display: "flex", flexDirection: "column", padding: "10px 0" }}>
              <CodeLine n="2512" sign="" text={'"select name, tbl_name, sql"'} />
              <CodeLine n="2513" sign="" text={'" from sqlite_master"'} />
              <CodeLine n="2514" sign="+" text={`f" and tbl_name = '{self.name}'",`} added />
            </div>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                margin: "0 18px 18px",
                padding: "16px 18px",
                borderRadius: 12,
                borderLeft: `4px solid ${C.critical}`,
                background: C.criticalSoft,
                gap: 10,
              }}
            >
              <div style={{ display: "flex", gap: 8, fontSize: 16 }}>
                <span style={{ display: "flex", padding: "2px 10px", borderRadius: 999, background: C.critical, color: C.background, fontWeight: 600 }}>
                  critical
                </span>
                <span style={{ display: "flex", padding: "2px 10px", borderRadius: 999, border: `1px solid ${C.border}`, color: C.muted }}>
                  security
                </span>
              </div>
              <div style={{ display: "flex", fontSize: 24, fontWeight: 600 }}>Potential SQL injection</div>
              <div style={{ display: "flex", fontSize: 17, color: C.muted, lineHeight: 1.4 }}>
                self.name goes into the query string unescaped.
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 4, alignItems: "center" }}>
                <span style={{ fontSize: 15, color: C.muted }}>Evidence</span>
                <span style={{ display: "flex", padding: "3px 10px", borderRadius: 8, border: `1px solid ${C.border}`, fontFamily: "Geist Mono", fontSize: 14, color: C.foreground }}>
                  Database.triggers
                </span>
                <span style={{ display: "flex", padding: "3px 10px", borderRadius: 8, border: `1px solid ${C.border}`, fontFamily: "Geist Mono", fontSize: 14, color: C.foreground }}>
                  Table.triggers_dict
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [
        { name: "Geist", data: regular, weight: 400, style: "normal" },
        { name: "Geist", data: semibold, weight: 600, style: "normal" },
        { name: "Geist Mono", data: mono, weight: 400, style: "normal" },
      ],
    },
  );
}
