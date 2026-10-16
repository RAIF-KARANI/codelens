/** Light, dark, or whatever the system says (the default). The choice lives in
 * localStorage, so it never reaches the server and the shell stays prerendered;
 * an inline script applies it before the first paint (`THEME_SCRIPT`). */

export type ThemeChoice = "system" | "light" | "dark";
export type Theme = "light" | "dark";

export const THEME_KEY = "codelens-theme";
export const DARK_QUERY = "(prefers-color-scheme: dark)";

/** A stored value as a choice: anything unknown (or nothing) means "system". */
export function parseThemeChoice(value: unknown): ThemeChoice {
  return value === "light" || value === "dark" ? value : "system";
}

/** The theme a choice shows, given whether the system prefers dark. */
export function resolveTheme(choice: ThemeChoice, systemPrefersDark: boolean): Theme {
  if (choice === "system") return systemPrefersDark ? "dark" : "light";
  return choice;
}

/** Runs in <head> while the page is parsed, before anything is painted: the same
 * rule as `resolveTheme`, written out because it can't import anything. A
 * blocked localStorage (private mode, settings) falls back to the system. */
export const THEME_SCRIPT = `(function(){var c;try{c=localStorage.getItem(${JSON.stringify(THEME_KEY)})}catch(e){}var d=c==="dark"||(c!=="light"&&matchMedia(${JSON.stringify(DARK_QUERY)}).matches);document.documentElement.setAttribute("data-theme",d?"dark":"light")})()`;
