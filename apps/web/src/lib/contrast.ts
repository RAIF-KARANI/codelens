/** Colour contrast, as WCAG measures it, and a way to keep a colour readable.
 * Used for highlighted code (lib/highlight.ts): GitHub's themes are made for
 * a plain background, and our diff rows are tinted green or red. */

type Rgb = [number, number, number];

export function parseHex(color: string): Rgb {
  let h = color.trim().replace(/^#/, "");
  if (h.length === 3 || h.length === 4) h = [...h.slice(0, 3)].map((c) => c + c).join("");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as Rgb;
}

export function toHex([r, g, b]: Rgb): string {
  return `#${[r, g, b].map((c) => Math.round(c).toString(16).padStart(2, "0")).join("")}`;
}

/** `top` at `alpha` over `bottom`, the way a translucent background paints. */
export function over(top: string, alpha: number, bottom: string): string {
  const t = parseHex(top);
  const b = parseHex(bottom);
  return toHex(t.map((c, i) => c * alpha + b[i] * (1 - alpha)) as Rgb);
}

function luminance(color: string): number {
  const linear = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  const [r, g, b] = parseHex(color);
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

/** WCAG's contrast ratio, 1 to 21. Normal text needs 4.5 (AA). */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** `color`, moved towards black (on light backgrounds) or white (on dark
 * ones) just far enough to reach `ratio` against every background. A colour
 * that already does is returned as it is, so most tokens keep their hue. */
export function readable(color: string, backgrounds: string[], ratio = 4.5): string {
  const worst = (c: string) => Math.min(...backgrounds.map((b) => contrast(c, b)));
  if (worst(color) >= ratio) return color;
  const dark = backgrounds.every((b) => luminance(b) < 0.2);
  const target = dark ? "#ffffff" : "#000000";
  for (let step = 1; step <= 20; step++) {
    const moved = over(target, step / 20, color);
    if (worst(moved) >= ratio) return moved;
  }
  return target;
}
