import type { CSSProperties } from "react";

import type { Token } from "@/lib/code";

/** One highlighted line: each token's colours as CSS variables (`.tok` in
 * globals.css picks the one for the current theme). */
export function CodeLine({ tokens }: { tokens: Token[] }) {
  return tokens.map((t, i) =>
    t.light || t.dark ? (
      <span key={i} className="tok" style={{ "--l": t.light, "--d": t.dark } as CSSProperties}>
        {t.text}
      </span>
    ) : (
      t.text
    ),
  );
}
