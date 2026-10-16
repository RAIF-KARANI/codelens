import { SIDEBAR_SCRIPT } from "@/lib/sidebar";
import { THEME_SCRIPT } from "@/lib/theme";

/** The one inline script in <head>: the theme, then the sidebar's state, both
 * applied before the first paint. Each is an expression, so they need a `;`
 * between them: without it, the second's parentheses would call the first's
 * result, and the sidebar's would never run. */
export const HEAD_SCRIPT = `${THEME_SCRIPT};${SIDEBAR_SCRIPT}`;
